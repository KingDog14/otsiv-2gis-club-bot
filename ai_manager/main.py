import asyncio
import logging
from aiogram import Bot, Dispatcher
from config import settings
from db.repos import init_db
from bot.handlers import client, manager


async def main():
    settings.validate()
    init_db()
    dp = Dispatcher()
    dp.include_router(manager.router)
    dp.include_router(client.router)
    async with Bot(settings.BOT_TOKEN) as bot:
        await dp.start_polling(bot)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
