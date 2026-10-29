import asyncio
import logging
import re
from collections import defaultdict
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from types import SimpleNamespace
from config import settings, BASE
from core import assistant, media
from core.tools import escalate, keyboard, save_lead
from db import repos

router = Router()
locks = defaultdict(asyncio.Lock)
HUMAN = re.compile(r'оператор|человек|менеджер', re.IGNORECASE)
PHONE = re.compile(r'(?<!\w)\+?\d[\d ()\-]{8,}\d(?!\w)')
QUESTION = re.compile(r'\?|сколько|стоимост|цен[аыуе]|как|когда|гаранти|ремонт|диагност|срок', re.IGNORECASE)
EMAIL = re.compile(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-zа-я]{2,}', re.IGNORECASE)


def extract_contact(text):
    email = EMAIL.search(text)
    if email:
        return email.group(0)
    for match in PHONE.finditer(text):
        if 10 <= sum(char.isdigit() for char in match.group(0)) <= 15:
            return match.group(0).strip()
    return None


async def send_and_record(message, tg_id, text):
    text = text[:4000]
    if tg_id == settings.OWNER_TG_ID and repos.is_escalated(tg_id):
        display = text[:3800] + '\n\nЧтобы задать следующий вопрос, завершите тестовое обращение кнопкой ниже.'
        await message.answer(display, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Продолжить проверку', callback_data='admin:test:resume')]]))
    else:
        await message.answer(text)
    repos.add_message(tg_id, 'assistant', text)


@router.message(CommandStart(), F.chat.type == 'private')
async def client_start(message: Message):
    if message.from_user.id == settings.OWNER_TG_ID:
        return
    repos.ensure_conversation(message.from_user.id, message.from_user.username)
    text = (f'🛠 {settings.COMPANY_NAME}\n\n'
            'Поможем разобраться с услугами и стоимостью. Опишите задачу или задайте вопрос — ответим по делу.')
    menu = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Оставить заявку', callback_data='client:order', style='success')],
        [InlineKeyboardButton(text='Услуги и цены', callback_data='client:prices', style='primary'),
         InlineKeyboardButton(text='Задать вопрос', callback_data='client:question')],
    ])
    try:
        await message.answer_photo(FSInputFile(BASE / 'assets' / 'description-picture.jpg'), caption=text, reply_markup=menu)
    except Exception:
        logging.exception('Unable to send welcome image')
        await message.answer(text, reply_markup=menu)


async def process_client(message, bot, text, contact=None):
    tg_id, username = message.from_user.id, message.from_user.username
    async with locks[tg_id]:
        repos.ensure_conversation(tg_id, username)
        history = repos.history(tg_id, 10)
        repos.add_message(tg_id, 'user', text)
        if repos.is_escalated(tg_id) and tg_id == settings.OWNER_TG_ID and not (contact or extract_contact(text)):
            await send_and_record(message, tg_id, 'Тестовое обращение уже передано менеджеру. Откройте /panel и завершите его в разделе «Обращения», чтобы продолжить тест.')
            return
        if repos.is_escalated(tg_id):
            contact = contact or extract_contact(text)
            saved = False
            if contact:
                question = next((content for role, content in reversed(history) if role == 'user'), text)
                try:
                    await save_lead(bot, tg_id, username, contact, question)
                    saved = True
                except Exception:
                    logging.exception('Failed to save lead while escalated')
            try:
                suffix = '\n⚠️ Контакт не сохранён в таблицу.' if contact and not saved else ''
                await bot.send_message(settings.OWNER_TG_ID, (f'💬 Клиент @{username or "—"} ({tg_id}):\n{text}' + suffix)[:4000], reply_markup=keyboard(tg_id))
                answer = 'Сообщение передано менеджеру.'
                if saved:
                    answer = 'Контакт сохранён и передан менеджеру.'
                elif contact:
                    answer = 'Контакт передан менеджеру, но сохранить его в таблицу не удалось.'
                await send_and_record(message, tg_id, answer)
            except Exception:
                logging.exception('Failed to forward client message')
                await send_and_record(message, tg_id, 'Не удалось передать сообщение. Пожалуйста, попробуйте позже.')
            return
        if HUMAN.search(text):
            try:
                await escalate(bot, tg_id, username, 'Клиент попросил менеджера')
                answer = 'Передаю ваш запрос менеджеру.'
                provided_contact = contact or extract_contact(text)
                if provided_contact:
                    question = next((content for role, content in reversed(history) if role == 'user'), text)
                    try:
                        await save_lead(bot, tg_id, username, provided_contact, question)
                        answer = 'Контакт сохранён, ваш запрос передан менеджеру.'
                    except Exception:
                        logging.exception('Failed to save contact in human request')
                        answer = 'Запрос и контакт переданы менеджеру, но сохранить контакт в таблицу не удалось.'
            except Exception:
                logging.exception('Failed to escalate')
                answer = 'Не удалось связаться с менеджером. Пожалуйста, попробуйте позже.'
            await send_and_record(message, tg_id, answer)
            return
        contact = contact or extract_contact(text)
        if contact:
            question = text if QUESTION.search(text) else next((content for role, content in reversed(history) if role == 'user'), text)
            try:
                await save_lead(bot, tg_id, username, contact, question)
                answer = 'Спасибо! Ваш контакт сохранён.'
                try:
                    # Continue with the same context even when the message only contains a contact.
                    detail = await assistant.prepare(bot, tg_id, username, history, text, lead_saved=True)
                    if detail.strip():
                        answer += '\n\n' + detail
                except Exception:
                    logging.exception('Failed to respond after saving contact')
                    if QUESTION.search(text):
                        try:
                            if not repos.is_escalated(tg_id):
                                await escalate(bot, tg_id, username, 'Не удалось ответить на вопрос после сохранения контакта')
                            answer += ' Ваш вопрос передан менеджеру.'
                        except Exception:
                            logging.exception('Failed to escalate after saving contact')
                            answer += ' Не удалось передать вопрос менеджеру; попробуйте позже.'
            except Exception:
                logging.exception('Failed to save lead')
                try:
                    await escalate(bot, tg_id, username, 'Не удалось сохранить контакт в Google Sheets')
                    answer = 'Не удалось сохранить контакт. Передаю его менеджеру.'
                except Exception:
                    logging.exception('Failed to escalate lead')
                    answer = 'Не удалось сохранить контакт или связаться с менеджером. Пожалуйста, попробуйте позже.'
            await send_and_record(message, tg_id, answer)
            return
        try:
            answer = await assistant.prepare(bot, tg_id, username, history, text)
        except Exception:
            logging.exception('GigaChat request failed')
            try:
                if not repos.is_escalated(tg_id):
                    await escalate(bot, tg_id, username, 'Ошибка GigaChat или инструмента')
                answer = 'Секунду, передаю менеджеру.'
            except Exception:
                logging.exception('Failed to escalate')
                answer = 'Не удалось связаться с менеджером. Пожалуйста, попробуйте позже.'
        await send_and_record(message, tg_id, answer[:4000])


@router.callback_query(F.data.startswith('client:'))
async def shortcut(callback: CallbackQuery, bot: Bot):
    if not callback.message or callback.message.chat.type != 'private':
        await callback.answer('Откройте бота в личном чате.', show_alert=True)
        return
    if callback.from_user.id == settings.OWNER_TG_ID:
        from bot.handlers.manager import test_mode
        if callback.from_user.id not in test_mode:
            await callback.answer('Для тестирования откройте /test.', show_alert=True)
            return
    queries = {'client:cost': 'Хочу узнать стоимость. Что нужно уточнить?',
               'client:order': 'Хочу оставить заявку. Как это сделать?',
               'client:prices': 'Какие услуги и цены?',
               'client:hours': 'Какой режим работы?',
               'client:human': 'Позовите менеджера'}
    if callback.data == 'client:question':
        await callback.answer()
        await callback.message.answer('Напишите ваш вопрос здесь — можно подробно, своими словами.')
        return
    text = queries.get(callback.data)
    if text is None:
        await callback.answer('Неизвестная команда')
        return
    await callback.answer()
    # Use the clicking user's identity; callback.message was sent by the bot.
    proxy = SimpleNamespace(from_user=callback.from_user, answer=callback.message.answer)
    await process_client(proxy, bot, text)


async def process_media(message, bot):
    kind = 'photo' if message.photo else 'voice'
    attachment = message.photo[-1] if message.photo else message.voice
    try:
        description = await media.extract(bot, attachment, kind)
    except Exception:
        logging.exception('Could not process incoming media')
        await message.answer('Не удалось разобрать файл. Пожалуйста, напишите вопрос текстом или отправьте файл размером до 8 МБ.')
        return
    label = 'Описание фото' if kind == 'photo' else 'Распознанная речь'
    caption = (message.caption or '').strip()
    text = f'{label}: {description}'
    if caption:
        text += f'\nКомментарий клиента: {caption}'
    await process_client(message, bot, text)


@router.message(F.chat.type == 'private', F.photo | F.voice)
async def client_media(message: Message, bot: Bot):
    if message.from_user.id != settings.OWNER_TG_ID:
        await process_media(message, bot)


@router.message(F.chat.type == 'private', F.text)
async def client_message(message: Message, bot: Bot):
    if message.from_user.id != settings.OWNER_TG_ID:
        await process_client(message, bot, message.text)


@router.message(F.chat.type == 'private', F.contact)
async def client_contact(message: Message, bot: Bot):
    if message.from_user.id == settings.OWNER_TG_ID:
        return
    # Do not accept another person's Telegram contact without their consent.
    if message.contact.user_id and message.contact.user_id != message.from_user.id:
        await message.answer('Пожалуйста, отправьте свой контакт или напишите номер телефона.')
        return
    await process_client(message, bot, f'Контакт: {message.contact.phone_number}', message.contact.phone_number)


@router.message(F.chat.type == 'private')
async def unsupported(message: Message):
    if message.from_user.id != settings.OWNER_TG_ID:
        await message.answer('Пожалуйста, напишите вопрос текстом или отправьте свой контакт.')
