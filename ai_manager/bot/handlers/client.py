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
            messages, fallback = await assistant.prepare(bot, tg_id, username, history, text)
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
        # Stream into a single Telegram message, throttling edits to avoid rate limits.
        sent = None
        answer = ''
        last_edit = 0.0
        loop = asyncio.get_running_loop()
        try:
            async for chunk in assistant.stream_final(messages):
                answer += chunk
                if not sent and answer.strip():
                    sent = await message.answer(answer[:4000])
                    last_edit = loop.time()
                elif sent and loop.time() - last_edit >= 1.2:
                    await sent.edit_text(answer[:4000])
                    last_edit = loop.time()
            if not answer.strip():
                raise RuntimeError('Пустой ответ потока')
            if sent and sent.text != answer[:4000]:
                await sent.edit_text(answer[:4000])
        except Exception:
            logging.exception('Streaming failed')
            # Never append a second, potentially contradictory answer to partial output.
            if not sent:
                answer = fallback or 'Не удалось получить ответ. Попробуйте ещё раз.'
                await message.answer(answer[:4000])
            else:
                answer = sent.text
        repos.add_message(tg_id, 'assistant', answer[:4000])
