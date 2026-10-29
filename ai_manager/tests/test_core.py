import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import assistant, tools
from bot.handlers.client import HUMAN
from db import repos
from config import settings
from pathlib import Path
import tempfile


class CoreTests(unittest.IsolatedAsyncioTestCase):
    def test_search_and_human(self):
        with patch.object(tools.gsheets, 'context', return_value=''):
            self.assertIn('2500', ' '.join(tools.search_knowledge('замена экрана смартфона')['results']))
            self.assertEqual(tools.search_knowledge('квазичастица')['status'], 'empty')
        for text in ('Позовите человека', 'оператор', 'менеджер'):
            self.assertTrue(HUMAN.search(text))

    async def test_function_loop_preserves_name_and_only_stops_on_stop(self):
        call = SimpleNamespace(name='search_knowledge', arguments=json.dumps({'query': 'кружка'}))
        tool_message = SimpleNamespace(function_call=call)
        tool_response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='function_call', message=tool_message)])
        final_response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='900 рублей'))])
        # Real SDK models are required when the assistant message is inserted into Chat.
        from gigachat.models import Messages, MessagesRole
        tool_response.choices[0].message = Messages(role=MessagesRole.ASSISTANT, content='', function_call={'name': 'search_knowledge', 'arguments': {'query': 'кружка'}})
        with patch.object(assistant, 'chat', side_effect=[tool_response, final_response]), patch.object(assistant.gsheets, 'context', return_value=''), patch.object(assistant, 'execute_tool', new=AsyncMock(return_value='{"status":"success"}')):
            answer = await assistant.prepare(None, 1, None, [], 'цена?')
            messages = assistant.chat.call_args_list[1].args[0].messages
            self.assertEqual(assistant.chat.call_args_list[0].args[0].model, settings.GIGACHAT_MODEL)
        self.assertEqual(answer, '900 рублей')
        self.assertEqual(messages[-1].role, 'function')
        self.assertEqual(messages[-1].name, 'search_knowledge')

    def test_history_and_escalation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(repos, 'settings', SimpleNamespace(DB_PATH=str(Path(tmp) / 'db.sqlite'))):
            repos.init_db()
            repos.ensure_conversation(123, 'test')
            for i in range(12):
                repos.add_message(123, 'user', str(i))
            self.assertEqual(len(repos.history(123)), 10)
            self.assertEqual(repos.history(123)[0][1], '2')
            repos.set_escalated(123, True, 'test')
            self.assertTrue(repos.is_escalated(123))
            repos.set_escalated(123, False)
            self.assertFalse(repos.is_escalated(123))


if __name__ == '__main__':
    unittest.main()


class AgentContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_lead_already_saved_disables_save_tool_and_keeps_history(self):
        response = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content='От 2500 рублей.'))])
        with patch.object(assistant, 'chat', return_value=response) as chat, patch.object(assistant.gsheets, 'context', return_value=''):
            answer = await assistant.prepare(None, 1, None, [('user', 'У меня смартфон')], 'Цена? +7 999 123-45-67', lead_saved=True)
        payload = chat.call_args.args[0]
        self.assertEqual(answer, 'От 2500 рублей.')
        self.assertNotIn('save_lead', [fn.name for fn in payload.functions])
        self.assertIn('У меня смартфон', [message.content for message in payload.messages])
        self.assertIn('уже сохранён', payload.messages[0].content)
