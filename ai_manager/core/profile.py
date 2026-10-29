"""Telegram profile metadata. Description picture is configured via BotFather."""
import logging
from aiogram.types import (BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
                           FSInputFile, InputProfilePhotoStatic)
from config import BASE
from config import settings


def profile_text(company):
    # Telegram caps description at 512 and short description at 120 characters.
    name = ' '.join(company.split())[:80] or 'Наша компания'
    description = (f'{name} — ваш помощник в Telegram. Ответим на вопросы об услугах, '
                   'поможем оформить обращение и при необходимости подключим менеджера. '
                   'Нажмите «Запустить» и расскажите, чем помочь.')
    short = f'{name}: ответы на вопросы, заявки и связь с менеджером.'
    return description[:512], short[:120]


async def configure_profile(bot):
    description, short = profile_text(settings.COMPANY_NAME)
    calls = [
        ('описание', lambda: bot.set_my_description(description=description)),
        ('краткое описание', lambda: bot.set_my_short_description(short_description=short)),
        ('команды клиента', lambda: bot.set_my_commands(
            commands=[BotCommand(command='start', description='Начать диалог')],
            scope=BotCommandScopeDefault())),
        ('команды владельца', lambda: bot.set_my_commands(
            commands=[BotCommand(command='start', description='Панель управления'),
                      BotCommand(command='test', description='Тестировать как клиент'),
                      BotCommand(command='panel', description='Выйти из теста в панель'),
                      BotCommand(command='leads', description='Последние лиды')],
            scope=BotCommandScopeChat(chat_id=settings.OWNER_TG_ID))),
    ]
    if settings.BOT_SETUP_AVATAR:
        calls.append(('аватар', lambda: bot.set_my_profile_photo(
            photo=InputProfilePhotoStatic(photo=FSInputFile(BASE / 'assets' / 'avatar.jpg')))))
    for name, call in calls:
        try:
            await call()
        except Exception:
            # Metadata failure should not prevent the bot from receiving messages.
            logging.exception('Не удалось настроить %s Telegram', name)
