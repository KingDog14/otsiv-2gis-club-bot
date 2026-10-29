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
