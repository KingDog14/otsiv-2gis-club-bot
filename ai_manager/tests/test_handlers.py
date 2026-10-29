import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import client, manager
from core import tools
from db import repos


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(repos, 'settings', SimpleNamespace(DB_PATH=str(Path(self.tmp.name) / 'db.sqlite')))
        self.db.start()
        repos.init_db()
        manager.sessions.clear()
        manager.test_mode.clear()
        self.bot = SimpleNamespace(send_message=AsyncMock(), edit_message_text=AsyncMock())
        self.msg = SimpleNamespace(from_user=SimpleNamespace(id=123, username='tester'), answer=AsyncMock())

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()
        manager.sessions.clear()
        manager.test_mode.clear()

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

    async def test_contact_saves_and_uses_previous_question(self):
        repos.ensure_conversation(123, 'tester')
        repos.add_message(123, 'user', 'Хочу купить кружку')
        with patch.object(tools.gsheets, 'save') as save, patch.object(client.assistant, 'prepare', new=AsyncMock(return_value='Чем ещё помочь?')) as model:
            await client.process_client(self.msg, self.bot, 'Мой номер +7 999 123-45-67')
        model.assert_awaited_once()
        self.assertTrue(model.await_args.kwargs['lead_saved'])
        self.assertEqual(save.call_args.args[3], 'Хочу купить кружку')
        self.assertEqual(repos.stats()[1], 1)
        self.assertIn('сохранён', self.msg.answer.await_args.args[0].lower())

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
        with patch.object(tools.gsheets, 'save', side_effect=RuntimeError('sheet unavailable')):
            await client.process_client(self.msg, self.bot, '+7 999 123-45-67')
        self.assertTrue(repos.is_escalated(123))
        self.assertEqual(repos.stats()[1], 0)
        self.assertIn('Не удалось сохранить', self.msg.answer.await_args.args[0])

    async def test_admin_real_test_starts_in_one_click_and_exits(self):
        owner_id = manager.settings.OWNER_TG_ID
        owner = SimpleNamespace(id=owner_id, username='owner')
        panel = SimpleNamespace(chat=SimpleNamespace(id=owner_id, type='private'), message_id=7, edit_text=AsyncMock())
        callback = SimpleNamespace(from_user=owner, message=panel, data='admin:test', answer=AsyncMock())
        await manager.navigation(callback, self.bot)
        self.assertIn(owner_id, manager.test_mode)
        self.assertIn('по-настоящему', panel.edit_text.await_args.args[0])
        msg = SimpleNamespace(from_user=owner, text='Что с гарантией?', answer=AsyncMock())
        with patch.object(client.assistant, 'prepare', new=AsyncMock(return_value='Гарантия 3 месяца.')) as model:
            await manager.owner_message(msg, self.bot)
        model.assert_awaited_once()
        self.assertEqual(repos.history(owner_id)[-1][1], 'Гарантия 3 месяца.')
        callback.data = 'admin:home'
        await manager.navigation(callback, self.bot)
        self.assertNotIn(owner_id, manager.test_mode)

    async def test_admin_test_real_lead(self):
        owner_id = manager.settings.OWNER_TG_ID
        manager.test_mode.add(owner_id)
        msg = SimpleNamespace(from_user=SimpleNamespace(id=owner_id, username='owner'),
                              text='+7 999 123-45-67', answer=AsyncMock())
        with patch.object(tools.gsheets, 'save') as save, patch.object(client.assistant, 'prepare', new=AsyncMock(return_value='Спасибо.')):
            await manager.owner_message(msg, self.bot)
        save.assert_called_once()
        self.assertEqual(save.call_args.args[0], owner_id)
        self.assertEqual(repos.stats()[1], 1)

    async def test_question_with_contact_saves_and_answers_once(self):
        with patch.object(tools.gsheets, 'save') as save, patch.object(client.assistant, 'prepare', new=AsyncMock(return_value='От 2500 рублей.')) as model:
            await client.process_client(self.msg, self.bot, 'Сколько стоит экран? Мой номер +7 999 123-45-67')
        save.assert_called_once()
        self.assertTrue(model.await_args.kwargs['lead_saved'])
        self.assertIn('2500', self.msg.answer.await_args.args[0])

    async def test_resume_test_closes_only_owner_escalation(self):
        owner_id = manager.settings.OWNER_TG_ID
        repos.ensure_conversation(owner_id, 'owner')
        repos.ensure_conversation(123, 'tester')
        repos.set_escalated(owner_id, True, 'test')
        repos.set_escalated(123, True, 'real')
        msg = SimpleNamespace(chat=SimpleNamespace(id=owner_id, type='private'), message_id=20,
                              edit_reply_markup=AsyncMock(), edit_text=AsyncMock())
        callback = SimpleNamespace(from_user=SimpleNamespace(id=owner_id), message=msg,
                                   data='admin:test:resume', answer=AsyncMock())
        await manager.navigation(callback, self.bot)
        self.assertFalse(repos.is_escalated(owner_id))
        self.assertTrue(repos.is_escalated(123))
        self.assertIn(owner_id, manager.test_mode)
        msg.edit_reply_markup.assert_awaited_once()
        msg.edit_text.assert_not_awaited()

    async def test_panel_replacement_removes_previous_panel_only(self):
        self.bot.delete_message = AsyncMock()
        self.bot.edit_message_reply_markup = AsyncMock()
        await manager.adopt_panel(self.bot, 10, 10, 100)
        self.bot.delete_message.assert_not_awaited()
        await manager.adopt_panel(self.bot, 10, 10, 101)
        self.bot.delete_message.assert_awaited_once_with(chat_id=10, message_id=100)
        self.assertEqual(repos.panel_for(10), (10, 101))

    async def test_panel_fallback_clears_old_keyboard(self):
        self.bot.delete_message = AsyncMock(side_effect=RuntimeError('cannot delete'))
        self.bot.edit_message_reply_markup = AsyncMock()
        repos.save_panel(10, 10, 100)
        await manager.adopt_panel(self.bot, 10, 10, 101)
        self.bot.edit_message_reply_markup.assert_awaited_once_with(chat_id=10, message_id=100, reply_markup=None)

    async def test_successful_lead_notifies_owner_without_false_failure(self):
        with patch.object(tools.gsheets, 'save'), patch.object(client.assistant, 'prepare', new=AsyncMock(return_value='Готово.')):
            await client.process_client(self.msg, self.bot, 'Мой номер +7 999 123-45-67')
        self.assertEqual(repos.stats()[1], 1)
        self.assertIn('Новая заявка', self.bot.send_message.await_args.args[1])
        self.assertIn('сохранён', self.msg.answer.await_args.args[0])

    async def test_lead_notification_failure_does_not_trigger_escalation(self):
        self.bot.send_message.side_effect = RuntimeError('owner unavailable')
        with patch.object(tools.gsheets, 'save'), patch.object(client.assistant, 'prepare', new=AsyncMock(return_value='Спасибо.')):
            await client.process_client(self.msg, self.bot, 'Мой номер +7 999 123-45-67')
        self.assertEqual(repos.stats()[1], 1)
        self.assertFalse(repos.is_escalated(123))
        self.assertIn('сохранён', self.msg.answer.await_args.args[0])

    async def test_follow_up_keeps_context_and_does_not_escalate_known_answer(self):
        with patch.object(client.assistant, 'prepare', new=AsyncMock(side_effect=['От 2500 рублей.', 'Гарантия 3 месяца.'])) as model:
            await client.process_client(self.msg, self.bot, 'Сколько стоит замена экрана?')
            await client.process_client(self.msg, self.bot, 'А какая гарантия?')
        self.assertEqual(model.await_args.args[3], [('user', 'Сколько стоит замена экрана?'), ('assistant', 'От 2500 рублей.')])
        self.assertFalse(repos.is_escalated(123))
        self.bot.send_message.assert_not_awaited()

    async def test_saved_contact_stays_saved_when_model_is_down(self):
        with patch.object(tools.gsheets, 'save'), patch.object(client.assistant, 'prepare', new=AsyncMock(side_effect=RuntimeError('offline'))):
            await client.process_client(self.msg, self.bot, '+7 999 123-45-67')
        self.assertEqual(repos.stats()[1], 1)
        self.assertFalse(repos.is_escalated(123))
        self.assertIn('сохранён', self.msg.answer.await_args.args[0])

    async def test_buy_handoff_without_model_or_payment(self):
        with patch.object(client.assistant, 'prepare', new=AsyncMock()) as model:
            await client.process_client(self.msg, self.bot, 'Хочу купить')
        model.assert_not_awaited()
        self.assertTrue(repos.is_escalated(123))
        self.assertIn('менеджеру', self.msg.answer.await_args.args[0])

    async def test_buy_with_contact_is_saved_and_handed_off(self):
        with patch.object(tools.gsheets, 'save') as save:
            await client.process_client(self.msg, self.bot, 'Хочу оплатить, +7 999 123-45-67')
        self.assertEqual(save.call_args.args[-1], 'buy')
        self.assertTrue(repos.is_escalated(123))
        self.assertEqual(repos.stats()[1], 1)
