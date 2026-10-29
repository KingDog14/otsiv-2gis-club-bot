import unittest
from types import SimpleNamespace
from unittest.mock import patch
import gspread
from core import gsheets


class FakeSheet:
    def __init__(self, title, rows):
        self.title = title
        self.rows = [list(row) for row in rows]
        self.changes = 0

    def row_values(self, row):
        return self.rows[row - 1][:] if len(self.rows) >= row else []

    def get_all_values(self):
        return [row[:] for row in self.rows]

    def update(self, *, values, range_name, value_input_option):
        assert range_name.startswith('A1:') and value_input_option == 'RAW'
        if not self.rows:
            self.rows.append([])
        self.rows[0] = list(values[0]) + self.rows[0][len(values[0]):]
        self.changes += 1

    def update_title(self, value):
        self.title = value
        self.changes += 1

    def freeze(self, **kwargs):
        pass

    def format(self, *args):
        pass


class FakeBook:
    def __init__(self, sheets):
        self.sheets = sheets

    def worksheets(self):
        return self.sheets[:]

    def worksheet(self, name):
        for sheet in self.sheets:
            if sheet.title == name:
                return sheet
        raise gspread.WorksheetNotFound(name)

    def add_worksheet(self, title, rows, cols):
        sheet = FakeSheet(title, [])
        self.sheets.append(sheet)
        return sheet


class MigrationTests(unittest.TestCase):
    def test_migrates_titles_and_headers_preserving_data_and_extra_columns(self):
        lead = FakeSheet('Лист1', [gsheets.LEAD_HEADERS + ['своё поле'], ['2026', '123', 'user', 'contact', 'вопрос', 'buy', 'new', 'доп']])
        service = FakeSheet('Services', [gsheets.CATALOG_HEADERS['Services'], ['Диагностика', '500', 'Описание', 'true']])
        news = FakeSheet('News', [gsheets.CATALOG_HEADERS['News'], ['График', 'до 18', 'true']])
        book = FakeBook([lead, service, news])
        with patch.object(gsheets, '_book', return_value=book), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='')):
            gsheets.ensure_workspace()
            changes = [sheet.changes for sheet in book.sheets]
            gsheets.ensure_workspace()
        self.assertEqual([s.title for s in book.sheets], ['Заявки', 'Услуги', 'Новости', 'Контекст'])
        self.assertEqual(lead.rows[0], gsheets.LEAD_HEADERS_RU + ['своё поле'])
        self.assertEqual(lead.rows[1], ['2026', '123', 'user', 'contact', 'вопрос', 'buy', 'new', 'доп'])
        self.assertEqual(service.rows[1][0], 'Диагностика')
        self.assertEqual(changes, [sheet.changes for sheet in book.sheets])

    def test_russian_context_is_used_in_retrieval(self):
        context = FakeSheet('Контекст', [gsheets.CATALOG_HEADERS_RU['Context'], ['График', 'До 18 часов', 'true']])
        book = FakeBook([context])
        with patch.object(gsheets, '_book', return_value=book):
            result = gsheets.context()
        self.assertIn('До 18 часов', result)

    def test_collision_stops_before_any_modification(self):
        lead = FakeSheet('Лист1', [gsheets.LEAD_HEADERS])
        one = FakeSheet('Services', [gsheets.CATALOG_HEADERS['Services']])
        two = FakeSheet('Услуги', [gsheets.CATALOG_HEADERS_RU['Services']])
        book = FakeBook([lead, one, two])
        with patch.object(gsheets, '_book', return_value=book), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='')):
            with self.assertRaisesRegex(ValueError, 'Оба листа'):
                gsheets.ensure_workspace()
        self.assertEqual([sheet.title for sheet in book.sheets], ['Лист1', 'Services', 'Услуги'])
        self.assertTrue(all(sheet.changes == 0 for sheet in book.sheets))

    def test_headerless_sheet_with_data_not_overwritten(self):
        sheet = FakeSheet('Services', [[], ['не терять']])
        with patch.object(gsheets, '_book', return_value=FakeBook([sheet])), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='')):
            with self.assertRaisesRegex(ValueError, 'без заголовков'):
                gsheets.ensure_workspace()
        self.assertEqual(sheet.rows, [[], ['не терять']])

    def test_explicit_custom_leads_name_remains_usable(self):
        lead = FakeSheet('Мои заявки', [gsheets.LEAD_HEADERS, ['data']])
        with patch.object(gsheets, '_book', return_value=FakeBook([lead])), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='Мои заявки')):
            gsheets.ensure_workspace()
        self.assertEqual(lead.title, 'Мои заявки')
        self.assertEqual(lead.rows[0], gsheets.LEAD_HEADERS_RU)
