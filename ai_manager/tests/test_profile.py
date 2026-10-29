import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from core.profile import profile_text, configure_profile


class ProfileTests(unittest.IsolatedAsyncioTestCase):
    def test_limits(self):
        description, short = profile_text('Компания ' * 100)
        self.assertLessEqual(len(description), 512)
        self.assertLessEqual(len(short), 120)
        self.assertIn('Запустить', description)

    async def test_metadata_failure_does_not_prevent_other_setup(self):
        bot = SimpleNamespace(set_my_description=AsyncMock(side_effect=RuntimeError('offline')),
                              set_my_short_description=AsyncMock(), set_my_commands=AsyncMock(), set_my_profile_photo=AsyncMock())
        await configure_profile(bot)
        bot.set_my_short_description.assert_awaited_once()
        self.assertEqual(bot.set_my_commands.await_count, 2)
        bot.set_my_profile_photo.assert_awaited_once()
