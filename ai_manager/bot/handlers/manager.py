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
lead_tokens = {}
selection_choices = {}


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
            f'💬 Диалоги: {dialogs}   ·   📋 Записано ботом: {leads}\n'
            f'🔔 Нужен ответ: {active}\n\n'
            'Выберите раздел или проверьте, как отвечает бот.')
    return text, markup([button(f'🔔 Требуют ответа · {active}', 'admin:queue:0'), button(f'📋 Заявки · {leads}', 'admin:leads')],
                        [button('🧪 Проверить бота', 'admin:test', 'success')],
                        [button('🗂 Услуги и контекст', 'admin:catalog', 'primary'), button('📊 Отчёт', 'admin:report')],
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


STATUS_LABELS = {'new': '🆕 Новая', 'in_progress': '🟡 В работе', 'done': '✅ Завершена', 'lost': '⚪️ Закрыта', 'Новая': '🆕 Новая', 'В работе': '🟡 В работе', 'Завершена': '✅ Завершена', 'Закрыта': '⚪️ Закрыта'}


def leads_view(rows):
    lead_tokens.clear()
    lines = ['📋 Заявки · Google Sheets', '', 'Выберите заявку, чтобы изменить статус.']
    buttons = []
    if not rows:
        lines.append('\nПока нет заявок.')
    for row in rows:
        values = row['values']
        token = secrets.token_urlsafe(6)
        lead_tokens[token] = row
        label = ('@' + values[2]) if values[2] else (values[1] or 'Клиент')
        status = STATUS_LABELS.get(values[6], values[6] or 'Без статуса')
        lines.append(f"\n{label[:40]} · {status[:40]}\n{values[4][:100]}")
        buttons.append([button(f'{label[:30]} · {status[:20]}'[:60], f'admin:lead:{token}')])
    buttons.append([button('↻ Обновить', 'admin:leads'), button('← Назад', 'admin:home')])
    return '\n'.join(lines)[:4000], markup(*buttons)


def lead_view(token, snapshot, note=''):
    date, tg_id, username, contact, question, intent, status = snapshot['values']
    label = '@' + username if username else tg_id or 'Клиент'
    text = (f'📋 Заявка · {label[:100]}\n\n'
            f'🕒 {date[:80]}\n📞 {contact[:150]}\n'
            f'💬 {question[:1200]}\n🎯 {intent[:100]}\n'
            f'Статус: {STATUS_LABELS.get(status, status or "Без статуса")}')
    if note:
        text += '\n\n' + note
    return text[:4000], markup(
        [button('🟡 В работу', f'admin:status:{token}:in_progress', 'primary'),
         button('✅ Завершена', f'admin:status:{token}:done', 'success')],
        [button('🆕 Новая', f'admin:status:{token}:new'), button('⚪️ Закрыта', f'admin:status:{token}:lost')],
        [button('← К заявкам', 'admin:leads')])


async def load_leads_view():
    try:
        return leads_view(await asyncio.to_thread(gsheets.recent_sheet_leads))
    except Exception:
        logging.exception('Cannot read leads sheet')
        return ('⚠️ Не удалось загрузить заявки из Google Sheets. Проверьте доступ, заголовки и LEADS_SHEET_NAME.',
                markup([button('↻ Повторить', 'admin:leads'), button('← Назад', 'admin:home')]))


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
    selection_choices.clear()
    lead_tokens.clear()
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
    selection_choices.clear()
    lead_tokens.clear()
    sessions.pop(message.from_user.id, None)
    catalog_modes.pop(message.from_user.id, None)
    pending_changes.pop(message.from_user.id, None)
    test_mode.add(message.from_user.id)
    await show_panel(message, bot, test_view())


@router.message(Command('leads'), F.func(owner))
async def leads(message: Message, bot: Bot):
    selection_choices.clear()
    catalog_modes.pop(message.from_user.id, None)
    pending_changes.pop(message.from_user.id, None)
    sessions.pop(message.from_user.id, None)
    test_mode.discard(message.from_user.id)
    await show_panel(message, bot, await load_leads_view())


def catalog_view(data, note=''):
    lines = ['🗂 Услуги, новости и контекст', '', 'Данные из Google Sheets · только активные записи']
    services = data['Services']
    news = data['News']
    context = data.get('Context', [])
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
    lines.append('\nКонтекст:')
    lines.extend(f"• {r['title'][:120]}" for r in context[:5])
    if not context:
        lines.append('Пока нет записей')
    if note:
        lines.extend(['', note])
    return '\n'.join(lines)[:4000], markup(
        [button('＋ Услуга', 'admin:add:Services', 'success'), button('＋ Новость', 'admin:add:News', 'success')],
        [button('✏️ Изменить цену', 'admin:add:Price', 'primary'), button('✏️ Изменить новость', 'admin:add:NewsEdit', 'primary')],
        [button('👁 Услуги', 'admin:add:ServiceToggle'), button('👁 Новости', 'admin:add:NewsToggle')],
        [button('＋ Контекст', 'admin:add:Context', 'success'), button('✏️ Изменить контекст', 'admin:add:ContextEdit', 'primary')],
        [button('👁 Контекст', 'admin:add:ContextToggle')],
        [button('↻ Обновить', 'admin:catalog'), button('← Назад', 'admin:home')])


FIELD_PROMPTS = {
    'Services': ('название услуги', 'цену или диапазон', 'описание услуги'),
    'News': ('заголовок новости', 'текст новости'),
    'Price': ('название услуги', 'новую цену'),
    'NewsEdit': ('заголовок новости', 'новый текст'),
    'Context': ('тему', 'содержание для агента'),
    'ContextEdit': ('тему', 'новое содержание'),
}


def input_view(state):
    fields = state['fields']
    prompts = FIELD_PROMPTS[state['kind']]
    step = len(fields)
    lines = [f'✍️ Шаг {step + 1} из {len(prompts)}', '', f'Отправьте {prompts[step]} одним сообщением.']
    if fields:
        lines.extend(['', 'Уже заполнено:'] + [f'• {value[:250]}' for value in fields])
    buttons = []
    if fields:
        buttons.append(button('← Назад', 'admin:wback'))
    buttons.append(button('✕ Отмена', 'admin:catalog'))
    return '\n'.join(lines), markup(buttons)


def selection_view(kind, data, page):
    selection_choices.clear()
    service = kind in ('Price', 'ServiceToggle')
    source = 'Services' if service else ('Context' if kind in ('ContextEdit', 'ContextToggle') else 'News')
    rows = data.get(source, [])
    namekey = 'name' if service else 'title'
    valuekey = 'price' if kind == 'Price' else ('text' if kind in ('NewsEdit', 'ContextEdit') else 'active')
    page = min(max(0, page), max(0, (len(rows) - 1) // 8))
    title = 'Выберите услугу' if service else ('Выберите тему' if source == 'Context' else 'Выберите новость')
    buttons = []
    for row in rows[page * 8:page * 8 + 8]:
        token = secrets.token_urlsafe(6)
        selection_choices[token] = (kind, row[namekey], row[valuekey])
        prefix = ('🟢 ' if row.get('_active', True) else '⚪️ ') if kind in ('ServiceToggle', 'NewsToggle', 'ContextToggle') else ''
        buttons.append([button((prefix + row[namekey])[:55], f'admin:select:{token}')])
    navigation = []
    if page:
        navigation.append(button('‹ Назад', f'admin:choose:{kind}:{page - 1}'))
    if (page + 1) * 8 < len(rows):
        navigation.append(button('Далее ›', f'admin:choose:{kind}:{page + 1}'))
    if navigation:
        buttons.append(navigation)
    buttons.append([button('✕ Отмена', 'admin:catalog')])
    return f'🗂 {title}\n\nСтраница {page + 1} · Всего: {len(rows)}', markup(*buttons)


@router.callback_query(F.data.startswith('admin:') | F.data.startswith('reply:') | F.data.startswith('end:'))
async def navigation(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != settings.OWNER_TG_ID or not callback.message or callback.message.chat.type != 'private':
        await callback.answer('Нет доступа', show_alert=True)
        return
    data = callback.data
    if not data.startswith(('admin:add:', 'admin:choose:', 'admin:select:')) and data != 'admin:wback':
        selection_choices.clear()
    if not (data.startswith(('admin:add:', 'admin:confirm:', 'admin:choose:', 'admin:select:')) or data == 'admin:wback'):
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
        elif data.startswith(('admin:add:', 'admin:choose:')):
            parts = data.split(':')
            kind = parts[2] if len(parts) > 2 else ''
            if kind not in ('Services', 'News', 'Context', 'Price', 'NewsEdit', 'ContextEdit', 'ServiceToggle', 'NewsToggle', 'ContextToggle'):
                await callback.answer('Неизвестный раздел')
                return
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            pending_changes.pop(callback.from_user.id, None)
            catalog_modes.pop(callback.from_user.id, None)
            if kind in ('Price', 'NewsEdit', 'ContextEdit', 'ServiceToggle', 'NewsToggle', 'ContextToggle'):
                page = int(parts[3]) if len(parts) > 3 else 0
                try:
                    view = selection_view(kind, await asyncio.to_thread(gsheets.catalog, kind in ('ServiceToggle', 'NewsToggle', 'ContextToggle')), page)
                except Exception:
                    logging.exception('Catalog selection failed')
                    view = ('⚠️ Не удалось прочитать таблицу.', markup([button('← К каталогу', 'admin:catalog')]))
            else:
                state = {'kind': kind, 'chat': callback.message.chat.id, 'panel': callback.message.message_id, 'fields': [], 'expected': None}
                catalog_modes[callback.from_user.id] = state
                view = input_view(state)
        elif data.startswith('admin:select:'):
            token = data.rsplit(':', 1)[-1]
            selected = selection_choices.pop(token, None)
            if not selected:
                await callback.answer('Список устарел. Откройте каталог заново.', show_alert=True)
                return
            kind, name, expected = selected
            selection_choices.clear()
            if kind in ('ServiceToggle', 'NewsToggle', 'ContextToggle'):
                is_active = expected.strip().lower() not in ('0', 'false', 'нет', 'no', 'off')
                target = 'false' if is_active else 'true'
                nonce = secrets.token_urlsafe(8)
                pending_changes[callback.from_user.id] = (nonce, kind, [name, target], expected)
                action = 'Скрыть' if is_active else 'Опубликовать'
                view = (f'{action} запись «{name[:200]}»?\n\nПосле подтверждения запись изменится в Google Sheets.',
                        markup([button('✅ Подтвердить', f'admin:confirm:{nonce}', 'success'), button('Отмена', 'admin:catalog')]))
                await edit(callback, view)
                await adopt_panel(bot, callback.from_user.id, callback.message.chat.id, callback.message.message_id)
                await callback.answer()
                return
            state = {'kind': kind, 'chat': callback.message.chat.id, 'panel': callback.message.message_id, 'fields': [name], 'expected': expected}
            catalog_modes[callback.from_user.id] = state
            view = input_view(state)
        elif data == 'admin:wback':
            state = catalog_modes.get(callback.from_user.id)
            if not state or not state['fields']:
                await callback.answer('Откройте раздел заново', show_alert=True)
                return
            if state['kind'] in ('Price', 'NewsEdit', 'ContextEdit') and len(state['fields']) == 1:
                kind = state['kind']
                catalog_modes.pop(callback.from_user.id, None)
                view = selection_view(kind, await asyncio.to_thread(gsheets.catalog), 0)
            else:
                state['fields'].pop()
                view = input_view(state)
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
                elif kind == 'ContextEdit':
                    await asyncio.to_thread(gsheets.update_context, fields[0], fields[1], expected)
                elif kind in ('ServiceToggle', 'NewsToggle', 'ContextToggle'):
                    sheet = {'ServiceToggle': 'Services', 'NewsToggle': 'News', 'ContextToggle': 'Context'}[kind]
                    await asyncio.to_thread(gsheets.set_active, sheet, fields[0], fields[1], expected)
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
        elif data == 'admin:report':
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            try:
                report = await asyncio.to_thread(gsheets.lead_report)
                text = (f"📊 Заявки · отчёт\n\nВсего: {report['total']}\n"
                        f"🆕 Новые: {report['new']}\n🟡 В работе: {report['in_progress']}\n"
                        f"✅ Завершены: {report['done']}\n⚪️ Закрыты: {report['lost']}\n"
                        f"Другие статусы: {report['other']}\n\nИсточник: Google Sheets.")
                view = (text, markup([button('↻ Обновить', 'admin:report'), button('← Назад', 'admin:home')]))
            except Exception:
                logging.exception('Cannot read lead report')
                view = ('⚠️ Не удалось загрузить отчёт из таблицы.', markup([button('↻ Повторить', 'admin:report'), button('← Назад', 'admin:home')]))
        elif data == 'admin:leads':
            test_mode.discard(callback.from_user.id)
            sessions.pop(callback.from_user.id, None)
            view = await load_leads_view()
        elif data.startswith('admin:lead:'):
            token = data.rsplit(':', 1)[-1]
            snapshot = lead_tokens.get(token)
            if not snapshot:
                await callback.answer('Список устарел. Обновите заявки.', show_alert=True)
                return
            view = lead_view(token, snapshot)
        elif data.startswith('admin:status:'):
            parts = data.split(':')
            if len(parts) != 4 or parts[3] not in gsheets.LEAD_STATUSES:
                await callback.answer('Некорректный статус')
                return
            token, status = parts[2], parts[3]
            snapshot = lead_tokens.pop(token, None)
            if not snapshot:
                await callback.answer('Заявка устарела. Обновите список.', show_alert=True)
                return
            try:
                await asyncio.to_thread(gsheets.update_lead_status, snapshot, status)
                rows = await asyncio.to_thread(gsheets.recent_sheet_leads)
                view = leads_view(rows)
            except Exception:
                logging.exception('Failed to update lead status')
                view = ('⚠️ Не удалось подтвердить изменение статуса. Обновите список и проверьте таблицу: при сбое сети запись могла сохраниться.',
                        markup([button('↻ Обновить заявки', 'admin:leads')]))
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
        state = catalog_modes[message.from_user.id]
        value = message.text.strip()
        if not value or len(value) > 1000:
            await message.answer('Пожалуйста, отправьте непустое значение до 1000 символов.')
            return
        state['fields'].append(value)
        kind = state['kind']
        if len(state['fields']) < len(FIELD_PROMPTS[kind]):
            text, keyboard = input_view(state)
        else:
            fields = list(state['fields'])
            expected = state['expected']
            token = secrets.token_urlsafe(8)
            pending_changes[message.from_user.id] = (token, kind, fields, expected)
            catalog_modes.pop(message.from_user.id, None)
            text = '🔎 Проверьте перед сохранением:\n\n' + '\n'.join(f'{label.capitalize()}: {field}' for label, field in zip(FIELD_PROMPTS[kind], fields))
            if expected is not None:
                text += f'\n\nТекущее значение: {expected}'
            keyboard = markup([button('✅ Сохранить', f'admin:confirm:{token}', 'success'), button('✕ Отмена', 'admin:catalog')])
        try:
            await bot.edit_message_text(chat_id=state['chat'], message_id=state['panel'], text=text[:4000], reply_markup=keyboard)
        except Exception:
            logging.exception('Could not update catalog wizard')
            catalog_modes.pop(message.from_user.id, None)
            pending_changes.pop(message.from_user.id, None)
            await message.answer('Не удалось обновить панель. Откройте /panel и повторите.')
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
