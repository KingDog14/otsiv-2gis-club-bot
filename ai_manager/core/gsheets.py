from datetime import datetime, timezone
import gspread
from config import settings, path_from_base


LEAD_HEADERS = ['datetime', 'tg_id', 'username', 'contact', 'question', 'intent', 'status']
LEAD_HEADERS_RU = ['дата и время', 'telegram id', 'имя пользователя', 'контакт', 'вопрос', 'интерес', 'статус']
LEAD_STATUS_RU = {'new': 'Новая', 'in_progress': 'В работе', 'done': 'Завершена', 'lost': 'Закрыта'}
LEAD_STATUSES = ('new', 'in_progress', 'done', 'lost')


def _valid_lead_header(values):
    header = [str(v).strip().lower() for v in values[:len(LEAD_HEADERS)]]
    return header in (LEAD_HEADERS, LEAD_HEADERS_RU)


def leads_sheet(book, create=False):
    """Select an explicitly configured sheet or exactly one matching header.

    Never guess when two lead sheets exist; that can hide real customer data.
    """
    name = settings.LEADS_SHEET_NAME
    if name:
        try:
            sheet = book.worksheet(name)
        except gspread.WorksheetNotFound:
            if not create:
                raise ValueError(f'Лист заявок {name!r} не найден')
            sheet = book.add_worksheet(title=name, rows=1000, cols=7)
        header = sheet.row_values(1)
        if not header and create:
            if sheet.get_all_values():
                raise ValueError('Лист заявок содержит данные без заголовков')
            sheet.append_row(LEAD_HEADERS_RU, value_input_option='RAW')
        elif not _valid_lead_header(header):
            raise ValueError('Неверные заголовки листа заявок')
        return sheet
    matches = [sheet for sheet in book.worksheets() if _valid_lead_header(sheet.row_values(1))]
    if len(matches) > 1:
        raise ValueError('Найдено несколько листов заявок; укажите LEADS_SHEET_NAME')
    if matches:
        return matches[0]
    if not create:
        return None
    try:
        sheet = book.worksheet('Заявки')
    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet(title='Заявки', rows=1000, cols=7)
    header = sheet.row_values(1)
    if not header:
        if sheet.get_all_values():
            raise ValueError('Лист заявок содержит данные без заголовков')
        sheet.append_row(LEAD_HEADERS_RU, value_input_option='RAW')
    elif not _valid_lead_header(header):
        raise ValueError('Лист Leads уже существует с другими заголовками')
    return sheet


def save(tg_id, username, contact, question, intent):
    sheet = leads_sheet(_book(), create=True)
    russian = [x.strip().lower() for x in sheet.row_values(1)[:7]] == LEAD_HEADERS_RU
    sheet.append_row([datetime.now(timezone.utc).isoformat(), str(tg_id), username or '', contact, question, intent, 'Новая' if russian else 'new'], value_input_option='RAW')


def recent_sheet_leads(limit=10):
    sheet = leads_sheet(_book())
    if sheet is None:
        return []
    rows = sheet.get_all_values()
    if not rows or not _valid_lead_header(rows[0]):
        raise ValueError('Неверные заголовки листа заявок')
    found = []
    for index, row in enumerate(rows[1:], 2):
        cells = (row + [''] * 7)[:7]
        if any(cells):
            found.append({'sheet': sheet.title, 'row': index, 'values': cells})
    return list(reversed(found[-limit:]))


def update_lead_status(snapshot, status):
    if status not in LEAD_STATUSES:
        raise ValueError('Неизвестный статус')
    sheet = leads_sheet(_book())
    if sheet is None or sheet.title != snapshot['sheet']:
        raise ValueError('Лист заявок изменился; обновите список')
    current = (sheet.row_values(snapshot['row']) + [''] * 7)[:7]
    if current != snapshot['values']:
        raise ValueError('Строка изменилась или переместилась; обновите список')
    # Google Sheets has no compare-and-swap: check and update are not atomic.
    russian = [x.strip().lower() for x in sheet.row_values(1)[:7]] == LEAD_HEADERS_RU
    sheet.update(values=[[LEAD_STATUS_RU[status] if russian else status]], range_name=f"G{snapshot['row']}", value_input_option='RAW')


CATALOG_HEADERS = {
    'Services': ['name', 'price', 'description', 'active'],
    'News': ['title', 'text', 'active'],
    'Context': ['title', 'text', 'active'],
}
CATALOG_TITLES = {'Services': 'Услуги', 'News': 'Новости', 'Context': 'Контекст'}
CATALOG_HEADERS_RU = {
    'Services': ['название', 'цена', 'описание', 'активно'],
    'News': ['заголовок', 'текст', 'активно'],
    'Context': ['тема', 'содержание', 'активно'],
}


def _valid_catalog_header(kind, row):
    normalized = [x.strip().lower() for x in row[:len(CATALOG_HEADERS[kind])]]
    return normalized in (CATALOG_HEADERS[kind], CATALOG_HEADERS_RU[kind])


def _catalog_sheet(book, kind):
    try:
        return book.worksheet(CATALOG_TITLES[kind])
    except gspread.WorksheetNotFound:
        return book.worksheet(kind)


def _book():
    return gspread.service_account(filename=str(path_from_base(settings.GSHEETS_CREDENTIALS_JSON))).open_by_key(settings.GSHEETS_SPREADSHEET_ID)


def catalog(include_inactive=False):
    """Read the latest data directly: do not silently serve old prices on error."""
    book = _book()
    result = {'_services_configured': False}
    for name, headers in CATALOG_HEADERS.items():
        try:
            sheet = _catalog_sheet(book, name)
        except gspread.WorksheetNotFound:
            result[name] = []
            continue
        rows = sheet.get_all_values()
        if not rows:
            result[name] = []
            continue
        if not _valid_catalog_header(name, rows[0]):
            raise ValueError(f'Неверные заголовки листа {name}; нужны: {", ".join(headers)}')
        if name == 'Services':
            result['_services_configured'] = any(any(row) for row in rows[1:])
        result[name] = []
        for row in rows[1:]:
            row = (row + [''] * len(headers))[:len(headers)]
            active = row[-1].strip().lower() not in ('0', 'false', 'нет', 'no', 'off')
            if not any(row) or (not active and not include_inactive):
                continue
            item = dict(zip(headers, row))
            item['_active'] = active
            result[name].append(item)
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
        sheet = _catalog_sheet(book, kind)
    except gspread.WorksheetNotFound:
        sheet = book.add_worksheet(title=CATALOG_TITLES[kind], rows=1000, cols=len(headers))
    first = sheet.row_values(1)
    if not first:
        sheet.append_row(CATALOG_HEADERS_RU[kind], value_input_option='RAW')
    elif not _valid_catalog_header(kind, first):
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
    for row in data['Context']:
        lines.append(f"Контекст: {row['title']}; содержание: {row['text']}")
    content = '\n'.join(lines)
    if len(content) > 30000:
        raise ValueError('Каталог превышает лимит 30000 символов; сократите активные записи')
    return content



def _update_field(kind, name, value, expected, column=2):
    """Update a unique row after checking its old value (not an atomic CAS)."""
    sheet = _catalog_sheet(_book(), kind)
    rows = sheet.get_all_values()
    if not rows or not _valid_catalog_header(kind, rows[0]):
        raise ValueError(f'Неверные заголовки листа {kind}')
    matches = [(i, row) for i, row in enumerate(rows[1:], 2) if row and row[0].strip().casefold() == name.strip().casefold()]
    if len(matches) != 1:
        raise ValueError('Запись не найдена или название неоднозначно')
    index, row = matches[0]
    current = row[column - 1] if len(row) >= column else ''
    if current != expected:
        raise ValueError('Данные изменились в таблице; повторите операцию')
    col = chr(ord('A') + column - 1)
    sheet.update(values=[[value]], range_name=f'{col}{index}', value_input_option='RAW')


def update_price(name, price, expected):
    _update_field('Services', name, price, expected)


def update_news(title, text, expected):
    _update_field('News', title, text, expected)


def set_active(kind, name, value, expected):
    if kind not in ('Services', 'News', 'Context') or value not in ('true', 'false'):
        raise ValueError('Недопустимое изменение')
    _update_field(kind, name, value, expected, column=len(CATALOG_HEADERS[kind]))


def update_context(title, text, expected):
    _update_field('Context', title, text, expected)


def ensure_workspace():
    """Preflight every sheet before changing anything; never merge/delete rows."""
    import logging
    book = _book()
    sheets = book.worksheets()
    by_title = {s.title: s for s in sheets}
    headers = {s.title: s.row_values(1) for s in sheets}
    actions = []

    def check_empty_or_valid(sheet, valid):
        row = headers[sheet.title]
        if row:
            if not valid(row):
                raise ValueError(f'Лист «{sheet.title}» имеет неизвестные заголовки; изменения не выполнены')
        elif sheet.get_all_values():
            raise ValueError(f'Лист «{sheet.title}» содержит данные без заголовков; изменения не выполнены')

    configured = settings.LEADS_SHEET_NAME
    if configured:
        lead = by_title.get(configured)
        if not lead:
            raise ValueError(f'Указанный лист заявок «{configured}» не найден; изменения не выполнены')
        check_empty_or_valid(lead, _valid_lead_header)
        # Explicit custom name is retained so existing configuration stays valid.
        lead_target = configured
    else:
        candidates = [s for s in sheets if _valid_lead_header(headers[s.title])]
        for name in ('Заявки', 'Leads'):
            if name in by_title and by_title[name] not in candidates:
                check_empty_or_valid(by_title[name], _valid_lead_header)
                candidates.append(by_title[name])
        if len(candidates) > 1:
            raise ValueError('Несколько листов заявок; задайте LEADS_SHEET_NAME. Изменения не выполнены')
        lead = candidates[0] if candidates else None
        # Reuse the empty default tab only if there are no existing leads sheets.
        if lead is None and 'Лист1' in by_title and not headers['Лист1'] and not by_title['Лист1'].get_all_values():
            lead = by_title['Лист1']
        lead_target = 'Заявки'
    if lead and lead.title != lead_target and lead_target in by_title:
        raise ValueError('Лист «Заявки» уже существует; изменения не выполнены')
    actions.append((lead, lead_target, LEAD_HEADERS_RU))

    for kind, target in CATALOG_TITLES.items():
        candidates = [by_title[name] for name in (target, kind) if name in by_title]
        if len(candidates) > 1:
            raise ValueError(f'Оба листа «{target}» и «{kind}» существуют; изменения не выполнены')
        sheet = candidates[0] if candidates else None
        if sheet:
            check_empty_or_valid(sheet, lambda row, k=kind: _valid_catalog_header(k, row))
        actions.append((sheet, target, CATALOG_HEADERS_RU[kind]))

    for sheet, target, header in actions:
        changed = False
        if sheet is None:
            sheet = book.add_worksheet(title=target, rows=1000, cols=len(header))
            changed = True
        current = sheet.row_values(1)
        if [str(x).strip().lower() for x in current[:len(header)]] != header:
            # Update only the known header cells; data rows and extra columns stay untouched.
            last = chr(ord('A') + len(header) - 1)
            sheet.update(values=[header], range_name=f'A1:{last}1', value_input_option='RAW')
            changed = True
        if sheet.title != target:
            sheet.update_title(target)
            changed = True
        if changed:
            try:
                sheet.freeze(rows=1)
                sheet.format(f'A1:{chr(ord("A") + len(header) - 1)}1', {
                    'backgroundColor': {'red': 0.10, 'green': 0.18, 'blue': 0.28},
                    'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                })
            except Exception:
                logging.warning('Не удалось оформить заголовок листа %s', target, exc_info=True)


def lead_report():
    sheet = leads_sheet(_book())
    if sheet is None:
        return {'total': 0, 'new': 0, 'in_progress': 0, 'done': 0, 'lost': 0, 'other': 0}
    rows = sheet.get_all_values()
    if not rows or not _valid_lead_header(rows[0]):
        raise ValueError('Неверные заголовки листа заявок')
    reverse = {v.lower(): k for k, v in LEAD_STATUS_RU.items()}
    result = {'total': 0, 'new': 0, 'in_progress': 0, 'done': 0, 'lost': 0, 'other': 0}
    for row in rows[1:]:
        if not any(row):
            continue
        status = row[6].strip().lower() if len(row) > 6 else ''
        status = reverse.get(status, status)
        result['total'] += 1
        result[status if status in LEAD_STATUSES else 'other'] += 1
    return result
