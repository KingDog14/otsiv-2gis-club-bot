import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

from core import gsheets, tools, media, assistant
from bot.handlers import client, manager


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.book = MagicMock()
        self.services = MagicMock()
        self.news = MagicMock()
        self.services.get_all_values.return_value = [
            ['name', 'price', 'description', 'active'],
            ['Диагностика', '700 ₽', 'при ремонте бесплатно', 'true'],
            ['Старая услуга', '100 ₽', 'не продаётся', 'false'],
        ]
        self.news.get_all_values.return_value = [['title', 'text', 'active'], ['График', 'В субботу до 17', 'true']]
        self.book.worksheet.side_effect = lambda n: {'Services': self.services, 'News': self.news}[n]

    def test_catalog_skips_inactive_and_search_includes_live_data(self):
        with patch.object(gsheets, '_book', return_value=self.book):
            data = gsheets.catalog()
            self.assertEqual(len(data['Services']), 1)
            found = tools.search_knowledge('Диагностика')
        self.assertIn('700 ₽', ' '.join(found['results']))
        self.assertEqual(data['News'][0]['title'], 'График')

    def test_change_price_requires_matching_old_value_and_raw_update(self):
        with patch.object(gsheets, '_book', return_value=self.book):
            with self.assertRaises(ValueError):
                gsheets.update_price('Диагностика', '1000 ₽', '500 ₽')
            gsheets.update_price('Диагностика', '=1000', '700 ₽')
        self.services.update.assert_called_once_with(values=[['=1000']], range_name='B2', value_input_option='RAW')

    def test_add_service_uses_raw_fields(self):
        self.services.row_values.return_value = ['name', 'price', 'description', 'active']
        with patch.object(gsheets, '_book', return_value=self.book):
            gsheets.add_entry('Services', ['Услуга', '100 ₽', '=1+1'])
        self.services.append_row.assert_called_once_with(['Услуга', '100 ₽', '=1+1', 'true'], value_input_option='RAW')

    def test_bad_header_is_error_not_silent_fallback(self):
        self.services.get_all_values.return_value = [['Название', 'Цена']]
        with patch.object(gsheets, '_book', return_value=self.book), self.assertRaises(ValueError):
            gsheets.catalog()

    def test_colored_admin_menu(self):
        with patch.object(manager.repos, 'stats', return_value=(3, 2, 1)), patch.object(manager.repos, 'active_count', return_value=1):
            _, markup = manager.home_view()
        self.assertTrue(any(b.style == 'success' for row in markup.inline_keyboard for b in row))
        self.assertTrue(any(b.style == 'primary' for row in markup.inline_keyboard for b in row))


class MediaTests(unittest.IsolatedAsyncioTestCase):
    def test_uploaded_file_deleted_and_media_model_explicit(self):
        client_mock = MagicMock()
        api = client_mock.__enter__.return_value
        api.upload_file.return_value.id_ = 'file-id'
        api.chat.return_value.choices = [SimpleNamespace(message=SimpleNamespace(content='Сломанный экран'))]
        with patch.object(media, 'build_client', return_value=client_mock):
            result = media.describe(b'image', 'photo')
        self.assertEqual(result, 'Сломанный экран')
        self.assertEqual(api.chat.call_args.args[0].messages[0].attachments, ['file-id'])
        api.delete_file.assert_called_once_with('file-id')

    def test_uploaded_file_deleted_on_error(self):
        client_mock = MagicMock()
        api = client_mock.__enter__.return_value
        api.upload_file.return_value.id_ = 'file-id'
        api.chat.side_effect = RuntimeError('offline')
        with patch.object(media, 'build_client', return_value=client_mock), self.assertRaises(RuntimeError):
            media.describe(b'voice', 'voice')
        api.delete_file.assert_called_once_with('file-id')

    async def test_media_transcription_becomes_client_message(self):
        message = SimpleNamespace(photo=None, voice=SimpleNamespace(file_id='voice', file_size=10), caption=None, answer=AsyncMock())
        bot = MagicMock()
        with patch.object(media, 'extract', new=AsyncMock(return_value='Не включается ноутбук')), patch.object(client, 'process_client', new=AsyncMock()) as process:
            await client.process_media(message, bot)
        self.assertIn('Не включается ноутбук', process.await_args.args[2])

    async def test_oversize_is_rejected_before_download(self):
        bot = SimpleNamespace(get_file=AsyncMock())
        with self.assertRaises(ValueError):
            await media.extract(bot, SimpleNamespace(file_id='f', file_size=media.MAX_BYTES + 1), 'voice')
        bot.get_file.assert_not_awaited()


class AdminCatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from db import repos
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(repos, 'settings', SimpleNamespace(DB_PATH=str(Path(self.tmp.name) / 'test.sqlite')))
        self.db_patch.start()
        repos.init_db()
        manager.catalog_modes.clear()
        manager.pending_changes.clear()
        self.owner = manager.settings.OWNER_TG_ID
        self.bot = SimpleNamespace(edit_message_text=AsyncMock(), delete_message=AsyncMock(), edit_message_reply_markup=AsyncMock())
        self.panel = SimpleNamespace(chat=SimpleNamespace(type='private', id=self.owner), message_id=11, edit_text=AsyncMock())
        self.callback = SimpleNamespace(from_user=SimpleNamespace(id=self.owner), message=self.panel, data='admin:add:Services', answer=AsyncMock())

    def tearDown(self):
        manager.catalog_modes.clear()
        manager.pending_changes.clear()
        manager.sessions.clear()
        manager.test_mode.clear()
        self.db_patch.stop()
        self.tmp.cleanup()

    async def test_add_requires_confirmation_and_only_writes_once(self):
        await manager.navigation(self.callback, self.bot)
        message = SimpleNamespace(from_user=SimpleNamespace(id=self.owner), text='Диагностика | 500 ₽ | Проверка', answer=AsyncMock())
        with patch.object(manager.gsheets, 'add_entry') as save, patch.object(manager.gsheets, 'catalog', return_value={'Services': [], 'News': []}):
            await manager.owner_message(message, self.bot)
            save.assert_not_called()
            token = manager.pending_changes[self.owner][0]
            self.callback.data = 'admin:confirm:' + token
            await manager.navigation(self.callback, self.bot)
            save.assert_called_once_with('Services', ['Диагностика', '500 ₽', 'Проверка'])
            await manager.navigation(self.callback, self.bot)
            save.assert_called_once()

    async def test_old_confirmation_cannot_commit_new_draft(self):
        manager.pending_changes[self.owner] = ('new-token', 'News', ['Заголовок', 'Текст'], None)
        self.callback.data = 'admin:confirm:old-token'
        with patch.object(manager.gsheets, 'add_entry') as save:
            await manager.navigation(self.callback, self.bot)
            save.assert_not_called()
        self.assertIn(self.owner, manager.pending_changes)

    async def test_non_owner_cannot_write(self):
        self.callback.from_user = SimpleNamespace(id=self.owner + 1)
        await manager.navigation(self.callback, self.bot)
        self.assertEqual(manager.catalog_modes, {})
        self.assertTrue(self.callback.answer.await_args.kwargs['show_alert'])

    async def test_news_edit_previews_and_confirm_writes_expected_value(self):
        self.callback.data = 'admin:add:NewsEdit'
        await manager.navigation(self.callback, self.bot)
        message = SimpleNamespace(from_user=SimpleNamespace(id=self.owner), text='График | Теперь до 18:00', answer=AsyncMock())
        catalog = {'Services': [], 'News': [{'title': 'График', 'text': 'До 17:00'}]}
        with patch.object(manager.gsheets, 'catalog', return_value=catalog), patch.object(manager.gsheets, 'update_news') as save:
            await manager.owner_message(message, self.bot)
            save.assert_not_called()
            token = manager.pending_changes[self.owner][0]
            self.callback.data = 'admin:confirm:' + token
            await manager.navigation(self.callback, self.bot)
            save.assert_called_once_with('График', 'Теперь до 18:00', 'До 17:00')

    async def test_switch_to_test_cancels_catalog_input(self):
        await manager.navigation(self.callback, self.bot)
        self.callback.data = 'admin:test'
        await manager.navigation(self.callback, self.bot)
        self.assertNotIn(self.owner, manager.catalog_modes)
        self.assertIn(self.owner, manager.test_mode)


class RetrievalTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_receives_current_sheet_context(self):
        result = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='Диагностика — 700 ₽.'))])
        with patch.object(assistant.gsheets, 'context', return_value='Услуга: Диагностика; цена: 700 ₽'), patch.object(assistant, 'chat', return_value=result) as chat:
            await assistant.prepare(None, 5, None, [], 'Сколько стоит диагностика?')
        self.assertIn('цена: 700 ₽', chat.call_args.args[0].messages[0].content)

    async def test_catalog_failure_does_not_silently_use_old_price(self):
        with patch.object(assistant.gsheets, 'context', side_effect=RuntimeError('offline')), patch.object(assistant, 'chat') as chat:
            with self.assertRaises(RuntimeError):
                await assistant.prepare(None, 5, None, [], 'Сколько стоит диагностика?')
        chat.assert_not_called()
