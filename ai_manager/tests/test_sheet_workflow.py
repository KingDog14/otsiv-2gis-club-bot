import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from core import gsheets
from bot.handlers import client, manager
from db import repos


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.sheet = MagicMock(title='Лист1')
        self.sheet.title = 'Лист1'
        self.sheet.row_values.return_value = gsheets.LEAD_HEADERS
        self.book = MagicMock()
        self.book.worksheets.return_value = [self.sheet]

    def test_writes_to_existing_russian_sheet(self):
        with patch.object(gsheets, '_book', return_value=self.book), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='')):
            gsheets.save(12, 'test', '+79991234567', 'вопрос', 'consult')
        self.sheet.append_row.assert_called_once()
        self.book.add_worksheet.assert_not_called()
        self.assertEqual(self.sheet.append_row.call_args.args[0][-1], 'new')

    def test_multiple_matching_sheets_refuses_ambiguous_write(self):
        second = MagicMock()
        second.row_values.return_value = gsheets.LEAD_HEADERS
        self.book.worksheets.return_value.append(second)
        with patch.object(gsheets, '_book', return_value=self.book), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='')):
            with self.assertRaisesRegex(ValueError, 'несколько'):
                gsheets.save(12, '', '123', 'вопрос', 'consult')
        self.sheet.append_row.assert_not_called()
        second.append_row.assert_not_called()

    def test_status_update_validates_row_before_raw_write(self):
        values = ['2026', '12', 'test', '123', 'вопрос', 'consult', 'new']
        self.sheet.row_values.side_effect = lambda index: gsheets.LEAD_HEADERS if index == 1 else values
        snapshot = {'sheet': 'Лист1', 'row': 2, 'values': list(values)}
        with patch.object(gsheets, '_book', return_value=self.book), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='')):
            gsheets.update_lead_status(snapshot, 'in_progress')
            self.sheet.update.assert_called_once_with(values=[['in_progress']], range_name='G2', value_input_option='RAW')
            self.sheet.update.reset_mock()
            snapshot['values'][4] = 'устаревший вопрос'
            with self.assertRaises(ValueError):
                gsheets.update_lead_status(snapshot, 'done')
            self.sheet.update.assert_not_called()

    def test_can_select_named_sheet(self):
        self.book.worksheet.return_value = self.sheet
        with patch.object(gsheets, '_book', return_value=self.book), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='Лист1')):
            gsheets.save(12, 'test', '123', 'вопрос', 'consult')
        self.book.worksheet.assert_called_with('Лист1')


class TypingTests(unittest.IsolatedAsyncioTestCase):
    async def test_typing_sent_and_failure_does_not_break_answer(self):
        bot = SimpleNamespace(send_chat_action=AsyncMock(side_effect=RuntimeError('telegram down')))
        async with client.typing(bot, 42):
            pass
        bot.send_chat_action.assert_awaited_once_with(chat_id=42, action='typing')


class LeadUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(repos, 'settings', SimpleNamespace(DB_PATH=str(Path(self.tmp.name) / 'db.sqlite')))
        self.db.start()
        repos.init_db()
        manager.lead_tokens.clear()
        self.owner = manager.settings.OWNER_TG_ID
        self.message = SimpleNamespace(chat=SimpleNamespace(id=self.owner, type='private'), message_id=1, edit_text=AsyncMock())
        self.bot = SimpleNamespace(delete_message=AsyncMock(), edit_message_reply_markup=AsyncMock())
        self.callback = SimpleNamespace(from_user=SimpleNamespace(id=self.owner), message=self.message, data='admin:leads', answer=AsyncMock())
        self.snapshot = {'sheet': 'Лист1', 'row': 2, 'values': ['2026', '12', 'user', '123', 'Ремонт', 'consult', 'new']}

    def tearDown(self):
        manager.lead_tokens.clear()
        self.db.stop()
        self.tmp.cleanup()

    async def test_status_buttons_update_actual_sheet(self):
        with patch.object(gsheets, 'recent_sheet_leads', return_value=[self.snapshot]), patch.object(gsheets, 'update_lead_status') as update:
            await manager.navigation(self.callback, self.bot)
            token = next(iter(manager.lead_tokens))
            self.callback.data = f'admin:status:{token}:in_progress'
            await manager.navigation(self.callback, self.bot)
        update.assert_called_once_with(self.snapshot, 'in_progress')

    async def test_non_owner_cannot_modify_status(self):
        manager.lead_tokens['abc'] = self.snapshot
        self.callback.from_user.id = self.owner + 1
        self.callback.data = 'admin:status:abc:done'
        with patch.object(gsheets, 'update_lead_status') as update:
            await manager.navigation(self.callback, self.bot)
        update.assert_not_called()
        self.assertTrue(self.callback.answer.await_args.kwargs['show_alert'])


class ReportTests(unittest.TestCase):
    def test_report_counts_russian_legacy_and_external_status(self):
        sheet = MagicMock()
        sheet.row_values.return_value = gsheets.LEAD_HEADERS_RU
        sheet.get_all_values.return_value = [gsheets.LEAD_HEADERS_RU,
            ['1', '1', '', '', 'A', '', 'Новая'], ['2', '2', '', '', 'B', '', 'in_progress'],
            ['3', '3', '', '', 'C', '', 'В работе'], ['4', '4', '', '', 'D', '', 'особый']]
        book = MagicMock()
        book.worksheets.return_value = [sheet]
        with patch.object(gsheets, '_book', return_value=book), patch.object(gsheets, 'settings', SimpleNamespace(LEADS_SHEET_NAME='')):
            report = gsheets.lead_report()
        self.assertEqual(report, {'total': 4, 'new': 1, 'in_progress': 2, 'done': 0, 'lost': 0, 'other': 1})
