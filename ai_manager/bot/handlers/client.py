import asyncio
import logging
import re
from collections import defaultdict
from aiogram import Router, F, Bot
from aiogram.types import Message
from config import settings
from core import assistant
from core.tools import escalate, keyboard
from db import repos

router = Router()
locks = defaultdict(asyncio.Lock)
HUMAN = re.compile(r'оператор|человек|человека|менеджер', re.IGNORECASE)


@router.message(F.chat.type == 'private', F.text)
async def client_message(message: Message, bot: Bot):
    if message.from_user.id == settings.OWNER_TG_ID:
        return
    tg_id, username, text = message.from_user.id, message.from_user.username, message.text
    async with locks[tg_id]:
        repos.ensure_conversation(tg_id, username)
        history = repos.history(tg_id, 10)
        repos.add_message(tg_id, 'user', text)
        if repos.is_escalated(tg_id):
            try:
                await bot.send_message(settings.OWNER_TG_ID, f'💬 Клиент @{username or "—"} ({tg_id}):\n{text}'[:4000], reply_markup=keyboard(tg_id))
                await message.answer('Сообщение передано менеджеру.')
            except Exception:
                logging.exception('Failed to forward client message')
                await message.answer('Не удалось передать сообщение. Пожалуйста, попробуйте позже.')
            return
        if HUMAN.search(text):
            try:
                await escalate(bot, tg_id, username, 'Клиент попросил менеджера')
                answer = 'Передаю ваш запрос менеджеру.'
            except Exception:
                logging.exception('Failed to escalate')
                answer = 'Не удалось связаться с менеджером. Пожалуйста, попробуйте позже.'
            await message.answer(answer)
            repos.add_message(tg_id, 'assistant', answer)
            return
        try:
            answer = await assistant.prepare(bot, tg_id, username, history, text)
        except Exception:
            logging.exception('GigaChat request failed')
            try:
                await escalate(bot, tg_id, username, 'Ошибка GigaChat или лимит вызовов инструментов')
                answer = 'Секунду, передаю менеджеру.'
            except Exception:
                logging.exception('Failed to escalate')
                answer = 'Не удалось связаться с менеджером. Пожалуйста, попробуйте позже.'
            await message.answer(answer)
            repos.add_message(tg_id, 'assistant', answer)
            return
        answer = answer[:4000]
        await message.answer(answer)
        repos.add_message(tg_id, 'assistant', answer)
