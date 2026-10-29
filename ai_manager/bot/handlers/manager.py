import logging
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from config import settings
from db import repos

router = Router()
sessions = {}


def owner(message):
    return bool(message.from_user and message.from_user.id == settings.OWNER_TG_ID and message.chat.type == 'private')


def button(text, data):
    return InlineKeyboardButton(text=text, callback_data=data)


def markup(*rows):
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def home_view():
    dialogs, leads, escalations = repos.stats()
    active = repos.active_count()
    text = (f'🏠 Панель администратора · {settings.COMPANY_NAME}\n\n'
            f'💬 Диалогов: {dialogs}\n📋 Лидов: {leads}\n'
            f'🔔 Ожидают менеджера: {active}\n📊 Всего обращений к менеджеру: {escalations}\n\n'
            'Выберите раздел. Для обновления нажмите «Обновить».')
    return text, markup([button('🔔 Обращения', 'admin:queue:0'), button('📋 Лиды', 'admin:leads')],
                        [button('🔄 Обновить', 'admin:home')])


def queue_view(page):
    page = max(0, page)
    rows = repos.active_conversations(6, page * 6)
    # An old button may reference an emptied last page.
    if not rows and page:
        page = max(0, (repos.active_count() - 1) // 6)
        rows = repos.active_conversations(6, page * 6)
    lines = ['🔔 Обращения к менеджеру', '', f'Открыто: {repos.active_count()} · Страница {page + 1}']
    buttons = []
    for tg_id, username, last in rows:
        label = '@' + username if username else str(tg_id)
        lines.append(f'\n{label}: {(last or "Нет сообщений")[:100]}')
        buttons.append([button(f'💬 {label}'[:60], f'admin:dialog:{tg_id}')])
    if not rows:
        lines.append('\nНет открытых обращений.')
    nav = []
    if page:
        nav.append(button('‹ Назад', f'admin:queue:{page - 1}'))
    if (page + 1) * 6 < repos.active_count():
        nav.append(button('Далее ›', f'admin:queue:{page + 1}'))
    if nav:
        buttons.append(nav)
    buttons.append([button('🏠 Главное меню', 'admin:home')])
    return '\n'.join(lines), markup(*buttons)


def leads_view():
    rows = repos.recent_leads()
    lines = ['📋 Последние лиды', '']
    if not rows:
        lines.append('Лидов пока нет.')
    for date, tg_id, username, contact, question, intent in rows:
        label = '@' + username if username else str(tg_id)
        lines.append(f'{date} · {label}\nКонтакт: {contact[:120]}\nЗапрос: {question[:150]}\nИнтерес: {(intent or "other")[:30]}\n')
    return '\n'.join(lines)[:4000], markup([button('🔄 Обновить', 'admin:leads'), button('🏠 Главное меню', 'admin:home')])


def dialog_view(tg_id, status=''):
    row = repos.conversation(tg_id)
    if not row:
        return 'Диалог не найден.', markup([button('🔔 Обращения', 'admin:queue:0')])
    _, username, active = row
    label = '@' + username if username else 'без username'
    lines = [f'💬 Клиент {label} · {tg_id}', f'Статус: {"ожидает менеджера" if active else "автоматический ответ"}', '', 'Последние сообщения:']
    for role, content in repos.history(tg_id, 6):
        lines.append(f'{"Клиент" if role == "user" else "Администратор"}: {content[:350]}')
    if status:
        lines.extend(['', status])
    buttons = []
    if active:
        buttons.append([button('✍️ Написать клиенту', f'admin:write:{tg_id}'), button('⏹ Завершить', f'end:{tg_id}')])
    buttons.append([button('🔔 Обращения', 'admin:queue:0'), button('🏠 Главное меню', 'admin:home')])
    return '\n'.join(lines)[:4000], markup(*buttons)


async def edit(callback, view):
    text, keyboard = view
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        if 'message is not modified' not in str(exc).lower():
            raise


@router.message(CommandStart(), F.func(owner))
async def start(message: Message):
    sessions.pop(message.from_user.id, None)
    text, keyboard = home_view()
    await message.answer(text, reply_markup=keyboard)


@router.message(Command('leads'), F.func(owner))
async def leads(message: Message):
    text, keyboard = leads_view()
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(F.data.startswith('admin:') | F.data.startswith('reply:') | F.data.startswith('end:'))
async def navigation(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != settings.OWNER_TG_ID or not callback.message or callback.message.chat.type != 'private':
        await callback.answer('Нет доступа', show_alert=True)
        return
    data = callback.data
    try:
        if data == 'admin:home':
            sessions.pop(callback.from_user.id, None)
            view = home_view()
        elif data == 'admin:leads':
            sessions.pop(callback.from_user.id, None)
            view = leads_view()
        elif data.startswith('admin:queue:'):
            sessions.pop(callback.from_user.id, None)
            view = queue_view(int(data.rsplit(':', 1)[1]))
        elif data.startswith('admin:dialog:'):
            tg_id = int(data.rsplit(':', 1)[1])
            sessions.pop(callback.from_user.id, None)
            view = dialog_view(tg_id)
        elif data.startswith(('admin:write:', 'reply:')):
            tg_id = int(data.rsplit(':', 1)[1])
            if not repos.is_escalated(tg_id):
                await callback.answer('Обращение уже завершено', show_alert=True)
                return
            # Save the panel message so the next owner text updates this same panel.
            sessions[callback.from_user.id] = (tg_id, callback.message.chat.id, callback.message.message_id)
            view = dialog_view(tg_id, '✍️ Напишите сообщение клиенту. Чтобы отменить, выберите другой раздел.')
        elif data.startswith('end:'):
            tg_id = int(data.rsplit(':', 1)[1])
            if not repos.is_escalated(tg_id):
                await callback.answer('Обращение уже завершено', show_alert=True)
                return
            repos.set_escalated(tg_id, False)
            if sessions.get(callback.from_user.id, (None,))[0] == tg_id:
                sessions.pop(callback.from_user.id, None)
            try:
                await bot.send_message(tg_id, 'Диалог с менеджером завершён. Вы снова можете обратиться к администратору.')
            except Exception:
                logging.exception('Could not notify client %s', tg_id)
            view = dialog_view(tg_id, 'Обращение завершено.')
        else:
            await callback.answer('Неизвестная команда')
            return
        await edit(callback, view)
        await callback.answer()
    except (ValueError, IndexError):
        await callback.answer('Некорректная команда')


@router.message(F.func(owner), F.text)
async def owner_message(message: Message, bot: Bot):
    session = sessions.get(message.from_user.id)
    if not session:
        await message.answer('Откройте панель командой /start и выберите обращение.')
        return
    tg_id, chat_id, panel_id = session
    if not repos.is_escalated(tg_id):
        sessions.pop(message.from_user.id, None)
        await message.answer('Обращение уже завершено. Откройте панель: /start')
        return
    try:
        await bot.send_message(tg_id, message.text)
    except Exception:
        logging.exception('Could not deliver manager reply to %s', tg_id)
        await message.answer('Не удалось доставить сообщение клиенту. Попробуйте ещё раз.')
        return
    repos.add_message(tg_id, 'assistant', message.text)
    # Stay in reply mode so the manager can send several messages.
    text, keyboard = dialog_view(tg_id, '✅ Отправлено. Можно написать ещё или завершить обращение.')
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=panel_id, text=text, reply_markup=keyboard)
    except TelegramBadRequest:
        logging.exception('Could not update owner panel')
        await message.answer('Отправлено. Откройте панель: /start')


@router.message(F.func(owner))
async def owner_unsupported(message: Message):
    await message.answer('Для ответа клиенту отправьте текст. Панель управления: /start')
