"""Exercise aiogram filters/routing, not only individual handler functions."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Update, Message, User, Chat, CallbackQuery
from bot.handlers import client, manager
from db import repos


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if method.__class__.__name__ == 'SendMessage':
            return SimpleNamespace(message_id=100 + len(self.calls))
        return True

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b''


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_test_mode_routes_text_to_agent(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(repos, 'settings', SimpleNamespace(DB_PATH=str(Path(tmp) / 'db.sqlite'))), \
             patch.object(manager, 'settings', SimpleNamespace(OWNER_TG_ID=123, COMPANY_NAME='Тест')), \
             patch.object(client, 'settings', SimpleNamespace(OWNER_TG_ID=123, COMPANY_NAME='Тест')), \
             patch.object(client.assistant, 'prepare', new=AsyncMock(return_value='Гарантия три месяца.')) as model:
            repos.init_db()
            manager.test_mode.clear()
            manager.sessions.clear()
            session = FakeSession()
            bot = Bot('123456:ABCdefghijklmnopqrstuvwxyz0123456789', session=session)
            dp = Dispatcher()
            dp.include_router(manager.router)
            dp.include_router(client.router)
            user = User(id=123, is_bot=False, first_name='Owner')
            chat = Chat(id=123, type='private')
            panel = Message(message_id=10, date=datetime.now(timezone.utc), chat=chat,
                            from_user=User(id=123456, is_bot=True, first_name='Bot'), text='Panel')
            query = CallbackQuery(id='q', from_user=user, chat_instance='1', message=panel, data='admin:test:start')
            await dp.feed_update(bot, Update(update_id=1, callback_query=query))
            msg = Message(message_id=11, date=datetime.now(timezone.utc), chat=chat, from_user=user, text='Какая гарантия?')
            await dp.feed_update(bot, Update(update_id=2, message=msg))
            model.assert_awaited_once()
            self.assertIn('Гарантия три месяца.', [getattr(method, 'text', '') for method in session.calls])
            # Reopening the dashboard replaces the previous bot panel, not the user's messages.
            for update_id in (3, 4):
                start = Message(message_id=10 + update_id, date=datetime.now(timezone.utc), chat=chat, from_user=user, text='/start')
                await dp.feed_update(bot, Update(update_id=update_id, message=start))
            deletes = [method.message_id for method in session.calls if method.__class__.__name__ == 'DeleteMessage']
            self.assertIn(10, deletes)
            self.assertEqual(len(deletes), 2)
            manager.test_mode.clear()
            await bot.session.close()
