from datetime import datetime, timezone
import gspread
from config import settings, path_from_base


def save(tg_id, username, contact, question, intent):
    client = gspread.service_account(filename=str(path_from_base(settings.GSHEETS_CREDENTIALS_JSON)))
    book = client.open_by_key(settings.GSHEETS_SPREADSHEET_ID)
    try:
        sheet = book.worksheet('Leads')
    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet(title='Leads', rows=1000, cols=7)
    if not sheet.row_values(1):
        sheet.append_row(['datetime', 'tg_id', 'username', 'contact', 'question', 'intent', 'status'], value_input_option='RAW')
    sheet.append_row([datetime.now(timezone.utc).isoformat(), str(tg_id), username or '', contact, question, intent, 'new'], value_input_option='RAW')


CATALOG_HEADERS = {
    'Services': ['name', 'price', 'description', 'active'],
    'News': ['title', 'text', 'active'],
}


def _book():
    return gspread.service_account(filename=str(path_from_base(settings.GSHEETS_CREDENTIALS_JSON))).open_by_key(settings.GSHEETS_SPREADSHEET_ID)


def catalog():
    """Read the latest data directly: do not silently serve old prices on error."""
    book = _book()
    result = {'_services_configured': False}
    for name, headers in CATALOG_HEADERS.items():
        try:
            sheet = book.worksheet(name)
        except gspread.WorksheetNotFound:
            result[name] = []
            continue
        rows = sheet.get_all_values()
        if not rows:
            result[name] = []
            continue
        if [x.strip().lower() for x in rows[0][:len(headers)]] != headers:
            raise ValueError(f'Неверные заголовки листа {name}; нужны: {", ".join(headers)}')
        if name == 'Services':
            result['_services_configured'] = True
        result[name] = []
        for row in rows[1:]:
            row = (row + [''] * len(headers))[:len(headers)]
            if not any(row) or row[-1].strip().lower() in ('0', 'false', 'нет', 'no', 'off'):
                continue
            result[name].append(dict(zip(headers, row)))
    return result


def add_entry(kind, fields):
    """Append only predefined columns, never a model-generated arbitrary edit."""
    if kind not in CATALOG_HEADERS:
        raise ValueError('Неизвестный раздел')
    headers = CATALOG_HEADERS[kind]
    if len(fields) != len(headers) - 1 or not all(str(v).strip() for v in fields):
        raise ValueError('Заполните все поля')
    book = _book()
    try:
        sheet = book.worksheet(kind)
    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet(title=kind, rows=1000, cols=len(headers))
    first = sheet.row_values(1)
    if not first:
        sheet.append_row(headers, value_input_option='RAW')
    elif [x.strip().lower() for x in first[:len(headers)]] != headers:
        raise ValueError(f'Неверные заголовки листа {kind}')
    sheet.append_row([*fields, 'true'], value_input_option='RAW')


def context():
    data = catalog()
    lines = []
    if data['_services_configured']:
        lines.append('Каталог услуг настроен: предлагай только активные услуги из таблицы и используй только их цены; старые цены и отсутствующие услуги из текстовой базы не предлагай.')
    for row in data['Services']:
        lines.append(f"Услуга: {row['name']}; цена: {row['price']}; описание: {row['description']}")
    for row in data['News']:
        lines.append(f"Новость: {row['title']}; текст: {row['text']}")
    content = '\n'.join(lines)
    if len(content) > 30000:
        raise ValueError('Каталог превышает лимит 30000 символов; сократите активные записи')
    return content



def _update_field(kind, name, value, expected):
    """Update a unique row after checking its old value (not an atomic CAS)."""
    sheet = _book().worksheet(kind)
    rows = sheet.get_all_values()
    if not rows or [x.strip().lower() for x in rows[0][:len(CATALOG_HEADERS[kind])]] != CATALOG_HEADERS[kind]:
        raise ValueError(f'Неверные заголовки листа {kind}')
    matches = [(i, row) for i, row in enumerate(rows[1:], 2) if row and row[0].strip().casefold() == name.strip().casefold()]
    if len(matches) != 1:
        raise ValueError('Запись не найдена или название неоднозначно')
    index, row = matches[0]
    current = row[1] if len(row) > 1 else ''
    if current != expected:
        raise ValueError('Данные изменились в таблице; повторите операцию')
    sheet.update(values=[[value]], range_name=f'B{index}', value_input_option='RAW')


def update_price(name, price, expected):
    _update_field('Services', name, price, expected)


def update_news(title, text, expected):
    _update_field('News', title, text, expected)
