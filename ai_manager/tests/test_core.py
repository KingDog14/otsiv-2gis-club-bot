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
        self.assertIn('900', ' '.join(tools.search_knowledge('сколько стоит кружка')['results']))
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
        with patch.object(assistant, 'chat', side_effect=[tool_response, final_response]), patch.object(assistant, 'execute_tool', new=AsyncMock(return_value='{"status":"success"}')):
            answer = await assistant.prepare(None, 1, None, [], 'цена?')
            messages = assistant.chat.call_args_list[1].args[0].messages
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
