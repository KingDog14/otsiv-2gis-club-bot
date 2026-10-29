import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import client, manager
from db import repos


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(repos, 'settings', SimpleNamespace(DB_PATH=str(Path(self.tmp.name) / 'db.sqlite')))
        self.db.start()
        repos.init_db()
        manager.sessions.clear()
        self.bot = SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock())
        self.msg = SimpleNamespace(from_user=SimpleNamespace(id=123, username='tester'), answer=AsyncMock())

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()
        manager.sessions.clear()

    async def test_human_bypasses_model_and_visible_in_queue(self):
        with patch.object(client.assistant, 'prepare', new=AsyncMock()) as model:
            await client.process_client(self.msg, self.bot, 'Позовите человека')
        model.assert_not_awaited()
        self.assertTrue(repos.is_escalated(123))
        self.assertIn('@tester', manager.queue_view(0)[0])
        self.assertIn('Передаю', self.msg.answer.await_args.args[0])

    async def test_model_failure_escalates(self):
        with patch.object(client.assistant, 'prepare', new=AsyncMock(side_effect=RuntimeError('offline'))):
            await client.process_client(self.msg, self.bot, 'Вопрос')
        self.assertTrue(repos.is_escalated(123))
        self.assertIn('передаю менеджеру', self.msg.answer.await_args.args[0])

    async def test_contact_saves_without_model_and_uses_previous_question(self):
        repos.ensure_conversation(123, 'tester')
        repos.add_message(123, 'user', 'Хочу купить кружку')
        with patch.object(client.gsheets, 'save') as save, patch.object(client.assistant, 'prepare', new=AsyncMock()) as model:
            await client.process_client(self.msg, self.bot, 'Мой номер +7 999 123-45-67')
        model.assert_not_awaited()
        self.assertEqual(save.call_args.args[3], 'Хочу купить кружку')
        self.assertEqual(repos.stats()[1], 1)
        self.assertIn('сохранил', self.msg.answer.await_args.args[0].lower())

    async def test_model_failure_after_escalation_does_not_duplicate(self):
        async def failing(*args):
            repos.set_escalated(123, True, 'model escalation')
            raise RuntimeError('offline')
        with patch.object(client.assistant, 'prepare', side_effect=failing):
            await client.process_client(self.msg, self.bot, 'Вопрос')
        self.assertEqual(repos.stats()[2], 1)
        self.bot.send_message.assert_not_awaited()

    async def test_owner_write_and_finish(self):
        repos.ensure_conversation(123, 'tester')
        repos.set_escalated(123, True, 'test')
        owner = SimpleNamespace(id=manager.settings.OWNER_TG_ID)
        panel = SimpleNamespace(chat=SimpleNamespace(id=owner.id, type='private'), message_id=9, edit_text=AsyncMock())
        callback = SimpleNamespace(from_user=owner, message=panel, data='admin:write:123', answer=AsyncMock())
        await manager.navigation(callback, self.bot)
        self.assertIn(owner.id, manager.sessions)
        owner_message = SimpleNamespace(from_user=owner, text='Здравствуйте!', answer=AsyncMock())
        await manager.owner_message(owner_message, self.bot)
        self.bot.send_message.assert_any_await(123, 'Здравствуйте!')
        self.assertEqual(repos.history(123)[-1], ('assistant', 'Здравствуйте!'))
        callback.data = 'end:123'
        await manager.navigation(callback, self.bot)
        self.assertFalse(repos.is_escalated(123))
        self.assertNotIn(owner.id, manager.sessions)

    async def test_unauthorized_callback(self):
        callback = SimpleNamespace(from_user=SimpleNamespace(id=manager.settings.OWNER_TG_ID + 1), message=None, data='admin:home', answer=AsyncMock())
        await manager.navigation(callback, self.bot)
        self.assertTrue(callback.answer.await_args.kwargs['show_alert'])

    def test_contact_recognition(self):
        self.assertEqual(client.extract_contact('пишите test@example.com'), 'test@example.com')
        self.assertEqual(client.extract_contact('мой номер +7 999 123-45-67'), '+7 999 123-45-67')
        self.assertIsNone(client.extract_contact('стоимость 2500 рублей'))

    async def test_notification_reply_enters_reply_mode(self):
        repos.ensure_conversation(123, 'tester')
        repos.set_escalated(123, True, 'test')
        owner = SimpleNamespace(id=manager.settings.OWNER_TG_ID)
        panel = SimpleNamespace(chat=SimpleNamespace(id=owner.id, type='private'), message_id=9, edit_text=AsyncMock())
        callback = SimpleNamespace(from_user=owner, message=panel, data='reply:123', answer=AsyncMock())
        await manager.navigation(callback, self.bot)
        self.assertEqual(manager.sessions[owner.id][0], 123)

    async def test_contact_save_failure_escalates(self):
        with patch.object(client.gsheets, 'save', side_effect=RuntimeError('sheet unavailable')):
            await client.process_client(self.msg, self.bot, '+7 999 123-45-67')
        self.assertTrue(repos.is_escalated(123))
        self.assertEqual(repos.stats()[1], 0)
        self.assertIn('Не удалось сохранить', self.msg.answer.await_args.args[0])
