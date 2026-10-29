import logging
import asyncio
import secrets
from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from config import settings
from db import repos
from core import gsheets

router = Router()
sessions = {}
test_mode = set()
catalog_modes = {}
pending_changes = {}


def owner(message):
    return bool(message.from_user and message.from_user.id == settings.OWNER_TG_ID and message.chat.type == 'private')


def button(text, data, style=None):
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def markup(*rows):
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


def home_view():
    dialogs, leads, escalations = repos.stats()
    active = repos.active_count()
    text = (f'🏠 {settings.COMPANY_NAME} · Управление\n\n'
            f'💬 Диалоги: {dialogs}   ·   📋 Заявки: {leads}\n'
            f'🔔 Нужен ответ: {active}\n\n'
            'Выберите раздел или проверьте, как отвечает бот.')
    return text, markup([button(f'🔔 Требуют ответа · {active}', 'admin:queue:0'), button(f'📋 Заявки · {leads}', 'admin:leads')],
                        [button('🧪 Проверить бота', 'admin:test', 'success')],
                        [button('🗂 Услуги и новости', 'admin:catalog', 'primary')],
                        [button('↻ Обновить', 'admin:home')])


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


async def adopt_panel(bot, owner_id, chat_id, message_id):
    """Keep one current panel, without deleting client/owner messages or alerts."""
    old = repos.panel_for(owner_id)
    repos.save_panel(owner_id, chat_id, message_id)
    if old and old != (chat_id, message_id):
        try:
            await bot.delete_message(chat_id=old[0], message_id=old[1])
        except Exception:
            # Old messages may no longer be deletable; remove stale controls if possible.
            try:
                await bot.edit_message_reply_markup(chat_id=old[0], message_id=old[1], reply_markup=None)
            except Exception:
                logging.warning('Could not remove previous admin panel', exc_info=True)


async def show_panel(message, bot, view):
    text, keyboard = view
    sent = await message.answer(text, reply_markup=keyboard)
    await adopt_panel(bot, message.from_user.id, message.chat.id, sent.message_id)


@router.message(CommandStart(), F.func(owner))
@router.message(Command('panel'), F.func(owner))
async def start(message: Message, bot: Bot):
    sessions.pop(message.from_user.id, None)
    test_mode.discard(message.from_user.id)
    catalog_modes.pop(message.from_user.id, None)
    pending_changes.pop(message.from_user.id, None)
    await show_panel(message, bot, home_view())


def test_view():
    return ('🧪 Проверка бота\n\n'
            'Напишите вопрос так, как его задал бы клиент. Бот учтёт переписку и ответит здесь.\n\n'
            'Важно: контакты и обращения сохраняются по-настоящему. Используйте тестовые данные.',
            markup([button('← Вернуться к управлению', 'admin:home')]))


@router.message(Command('test'), F.func(owner))
async def test_command(message: Message, bot: Bot):
    sessions.pop(message.from_user.id, None)
    catalog_modes.pop(message.from_user.id, None)
    pending_changes.pop(message.from_user.id, None)
    test_mode.add(message.from_user.id)
    await show_panel(message, bot, test_view())


@router.message(Command('leads'), F.func(owner))
async def leads(message: Message, bot: Bot):
    catalog_modes.pop(message.from_user.id, None)
    pending_changes.pop(message.from_user.id, None)
    sessions.pop(message.from_user.id, None)
    test_mode.discard(message.from_user.id)
    await show_panel(message, bot, leads_view())


def catalog_view(data, note=''):
    lines = ['🗂 Услуги и новости', '', 'Данные из Google Sheets · только активные записи']
    services = data['Services']
    news = data['News']
    lines.append('\nУслуги:')
    lines.extend(f"• {r['name'][:100]} — {r['price'][:80]}" for r in services[:8])
    if not services:
        lines.append('Пока нет записей')
    if len(services) > 8:
        lines.append(f'… и ещё {len(services) - 8}')
    lines.append('\nНовости:')
    lines.extend(f"• {r['title'][:120]}" for r in news[:5])
    if not news:
        lines.append('Пока нет записей')
    if note:
        lines.extend(['', note])
    return '\n'.join(lines)[:4000], markup(
        [button('＋ Услуга', 'admin:add:Services', 'success'), button('＋ Новость', 'admin:add:News', 'success')],
        [button('✏️ Изменить цену', 'admin:add:Price', 'primary'), button('✏️ Изменить новость', 'admin:add:NewsEdit', 'primary')],
        [button('↻ Обновить', 'admin:catalog'), button('← Назад', 'admin:home')])


def input_view(kind):
    examples = {
        'Services': 'название | цена | описание\nНапример: Диагностика | 500 ₽ | Бесплатно при ремонте',
        'News': 'заголовок | текст\nНапример: График | 1 мая работаем до 16:00',
        'Price': 'название услуги | новая цена\nНапример: Диагностика | 600 ₽',
        'NewsEdit': 'заголовок новости | новый текст\nНапример: График | Работаем до 18:00',
    }
    return ('✍️ Отправьте одной строкой:\n\n' + examples[kind] +
            '\n\nИзменение будет показано для подтверждения.',
            markup([button('← Отмена', 'admin:catalog')]))


@router.callback_query(F.data.startswith('admin:') | F.data.startswith('reply:') | F.data.startswith('end:'))
async def navigation(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != settings.OWNER_TG_ID or not callback.message or callback.message.chat.type != 'private':
        await callback.answer('Нет доступа', show_alert=True)
        return
    data = callback.data
    if not (data.startswith('admin:add:') or data.startswith('admin:confirm:')):
        catalog_modes.pop(callback.from_user.id, None)
        pending_changes.pop(callback.from_user.id, None)
    try:
        if data == 'admin:catalog':
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            catalog_modes.pop(callback.from_user.id, None)
            pending_changes.pop(callback.from_user.id, None)
            try:
                catalog_data = await asyncio.to_thread(gsheets.catalog)
                view = catalog_view(catalog_data)
            except Exception:
                logging.exception('Catalog read failed')
                view = ('Не удалось загрузить таблицу. Проверьте доступ и заголовки листов.',
                        markup([button('↻ Повторить', 'admin:catalog'), button('← Назад', 'admin:home')]))
        elif data.startswith('admin:add:'):
            kind = data.rsplit(':', 1)[-1]
            if kind not in ('Services', 'News', 'Price', 'NewsEdit'):
                await callback.answer('Неизвестный раздел')
                return
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            pending_changes.pop(callback.from_user.id, None)
            catalog_modes[callback.from_user.id] = (kind, callback.message.chat.id, callback.message.message_id)
            view = input_view(kind)
        elif data.startswith('admin:confirm:'):
            pending = pending_changes.get(callback.from_user.id)
            token = data.rsplit(':', 1)[-1]
            if not pending or pending[0] != token:
                await callback.answer('Подтверждение устарело', show_alert=True)
                return
            pending_changes.pop(callback.from_user.id, None)
            catalog_modes.pop(callback.from_user.id, None)
            _, kind, fields, expected = pending
            try:
                if kind == 'Price':
                    await asyncio.to_thread(gsheets.update_price, fields[0], fields[1], expected)
                elif kind == 'NewsEdit':
                    await asyncio.to_thread(gsheets.update_news, fields[0], fields[1], expected)
                else:
                    await asyncio.to_thread(gsheets.add_entry, kind, fields)
                note = '✅ Изменение сохранено в таблице.'
            except Exception:
                logging.exception('Catalog write failed')
                note = '⚠️ Не удалось подтвердить запись. Проверьте таблицу перед повтором: при сбое сети запись могла сохраниться.'
            try:
                view = catalog_view(await asyncio.to_thread(gsheets.catalog), note)
            except Exception:
                view = (note, markup([button('← К каталогу', 'admin:catalog')]))
        elif data == 'admin:test:resume':
            # This button can only close the owner's own test conversation.
            if repos.is_escalated(callback.from_user.id):
                repos.set_escalated(callback.from_user.id, False)
            sessions.pop(callback.from_user.id, None)
            test_mode.add(callback.from_user.id)
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.answer('Можно задать следующий вопрос')
            return
        elif data in ('admin:test', 'admin:test:start'):
            sessions.pop(callback.from_user.id, None)
            test_mode.add(callback.from_user.id)
            view = test_view()
        elif data == 'admin:home':
            catalog_modes.pop(callback.from_user.id, None)
            pending_changes.pop(callback.from_user.id, None)
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            view = home_view()
        elif data == 'admin:leads':
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            view = leads_view()
        elif data.startswith('admin:queue:'):
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            view = queue_view(int(data.rsplit(':', 1)[1]))
        elif data.startswith('admin:dialog:'):
            test_mode.discard(callback.from_user.id)
            tg_id = int(data.rsplit(':', 1)[1])
            sessions.pop(callback.from_user.id, None)
            view = dialog_view(tg_id)
        elif data.startswith(('admin:write:', 'reply:')):
            test_mode.discard(callback.from_user.id)
            tg_id = int(data.rsplit(':', 1)[1])
            if not repos.is_escalated(tg_id):
                await callback.answer('Обращение уже завершено', show_alert=True)
                return
            # Save the panel message so the next owner text updates this same panel.
            sessions[callback.from_user.id] = (tg_id, callback.message.chat.id, callback.message.message_id)
            view = dialog_view(tg_id, '✍️ Напишите сообщение клиенту. Чтобы отменить, выберите другой раздел.')
        elif data.startswith('end:'):
            test_mode.discard(callback.from_user.id)
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
        if data.startswith('admin:'):
            await adopt_panel(bot, callback.from_user.id, callback.message.chat.id, callback.message.message_id)
        await callback.answer()
    except (ValueError, IndexError):
        await callback.answer('Некорректная команда')


@router.message(F.func(owner), F.text)
async def owner_message(message: Message, bot: Bot):
    if message.from_user.id in catalog_modes:
        kind, chat_id, panel_id = catalog_modes[message.from_user.id]
        fields = [field.strip() for field in message.text.split('|')]
        count = {'Services': 3, 'News': 2, 'Price': 2, 'NewsEdit': 2}[kind]
        if len(fields) != count or not all(fields) or any(len(field) > 1000 for field in fields):
            await message.answer('Проверьте поля: разделите их символом |, заполните все поля (до 1000 символов).')
            return
        expected = None
        if kind in ('Price', 'NewsEdit'):
            try:
                data = await asyncio.to_thread(gsheets.catalog)
                rows = data['Services' if kind == 'Price' else 'News']
                key = 'name' if kind == 'Price' else 'title'
                found = [r for r in rows if r[key].strip().casefold() == fields[0].casefold()]
                if len(found) != 1:
                    await message.answer('Активная запись не найдена или название неоднозначно. Укажите точное название.')
                    return
                expected = found[0]['price' if kind == 'Price' else 'text']
            except Exception:
                logging.exception('Could not check price')
                await message.answer('Не удалось проверить таблицу. Попробуйте позже.')
                return
        token = secrets.token_urlsafe(8)
        pending_changes[message.from_user.id] = (token, kind, fields, expected)
        catalog_modes.pop(message.from_user.id, None)
        text = 'Проверьте перед сохранением:\n\n' + '\n'.join(fields)
        if expected is not None:
            text += f'\nТекущее значение: {expected}'
        keyboard = markup([button('✅ Сохранить', f'admin:confirm:{token}', 'success'), button('Отмена', 'admin:catalog')])
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=panel_id, text=text[:4000], reply_markup=keyboard)
        except Exception:
            logging.exception('Could not show confirmation')
            pending_changes.pop(message.from_user.id, None)
            await message.answer('Не удалось показать подтверждение. Откройте /panel и повторите.')
        return
    if message.from_user.id in test_mode:
        from bot.handlers.client import process_client
        await process_client(message, bot, message.text)
        return
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


@router.message(F.func(owner), F.photo | F.voice)
async def owner_media(message: Message, bot: Bot):
    if message.from_user.id in test_mode:
        from bot.handlers.client import process_media
        await process_media(message, bot)
    else:
        await message.answer('Для проверки фото или голоса откройте /test.')


@router.message(F.func(owner), F.contact)
async def owner_contact(message: Message, bot: Bot):
    if message.from_user.id in test_mode:
        from bot.handlers.client import process_client
        if message.contact.user_id and message.contact.user_id != message.from_user.id:
            await message.answer('Отправьте свой контакт, не контакт другого человека.')
            return
        await process_client(message, bot, f'Контакт: {message.contact.phone_number}', message.contact.phone_number)
    else:
        await message.answer('Панель управления: /start')


@router.message(F.func(owner))
async def owner_unsupported(message: Message):
    await message.answer('Для ответа клиенту отправьте текст. Панель управления: /start')
