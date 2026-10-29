from aiogram import Router, F, Bot
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from config import settings
from core.tools import keyboard
from db import repos

router = Router()
# One active reply session per owner; escalation state persists in SQLite.
sessions = {}


def owner(message):
    return message.from_user and message.from_user.id == settings.OWNER_TG_ID and message.chat.type == 'private'


@router.message(CommandStart(), F.func(owner))
async def start(message: Message):
    dialogs, leads, escalations = repos.stats()
    await message.answer(f'Диалогов: {dialogs}. Лидов: {leads}. Эскалаций: {escalations}.')


@router.message(Command('leads'), F.func(owner))
async def leads(message: Message):
    rows = repos.recent_leads()
    if not rows:
        await message.answer('Лидов пока нет.')
        return
    for date, tg_id, username, contact, question, intent in rows:
        await message.answer(f'{date} | {tg_id} | @{username or "—"}\nКонтакт: {contact}\nВопрос: {question}\nИнтерес: {intent}'[:4000])


@router.callback_query(F.data.startswith('reply:'))
async def reply(callback: CallbackQuery):
    if callback.from_user.id != settings.OWNER_TG_ID or not callback.message or callback.message.chat.type != 'private':
        await callback.answer('Нет доступа', show_alert=True)
        return
    try:
        tg_id = int(callback.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await callback.answer('Некорректный диалог')
        return
    if not repos.is_escalated(tg_id):
        await callback.answer('Диалог уже завершён')
        return
    sessions[callback.from_user.id] = tg_id
    await callback.answer()
    await callback.message.answer(f'Режим ответа клиенту {tg_id}. Напишите сообщение.', reply_markup=keyboard(tg_id))


@router.callback_query(F.data.startswith('end:'))
async def end(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != settings.OWNER_TG_ID or not callback.message or callback.message.chat.type != 'private':
        await callback.answer('Нет доступа', show_alert=True)
        return
    try:
        tg_id = int(callback.data.split(':', 1)[1])
    except (ValueError, IndexError):
        await callback.answer('Некорректный диалог')
        return
    if not repos.is_escalated(tg_id):
        await callback.answer('Диалог уже завершён')
        return
    repos.set_escalated(tg_id, False)
    if sessions.get(callback.from_user.id) == tg_id:
        sessions.pop(callback.from_user.id, None)
    await callback.answer('Завершено')
    await callback.message.answer(f'Диалог {tg_id} завершён, администратор снова отвечает автоматически.')
    try:
        await bot.send_message(tg_id, 'Диалог с менеджером завершён. Вы снова можете обратиться к администратору.')
    except Exception:
        import logging
        logging.exception('Could not notify client')


@router.message(F.func(owner), F.text)
async def owner_message(message: Message, bot: Bot):
    tg_id = sessions.get(message.from_user.id)
    if not tg_id or not repos.is_escalated(tg_id):
        sessions.pop(message.from_user.id, None)
        await message.answer('Выберите «📥 Ответить» в эскалации.')
        return
    try:
        await bot.send_message(tg_id, message.text)
    except Exception:
        await message.answer('Не удалось доставить сообщение клиенту.')
        return
    repos.add_message(tg_id, 'assistant', message.text)
    await message.answer('Отправлено.', reply_markup=keyboard(tg_id))
