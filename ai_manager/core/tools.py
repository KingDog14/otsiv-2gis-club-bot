import asyncio
import json
import re
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from gigachat.models import Function, FunctionParameters
from config import settings, path_from_base
from core import gsheets
from db import repos

functions = [
    Function(name='search_knowledge', description='Ищет ответ в базе знаний. Вызывай первым для любого вопроса.', parameters=FunctionParameters(type='object', properties={'query': {'type': 'string'}}, required=['query'])),
    Function(name='save_lead', description='Сохраняет лид в Google Sheets. Вызывай, когда клиент оставил контакт или готов купить.', parameters=FunctionParameters(type='object', properties={'contact': {'type': 'string'}, 'question': {'type': 'string'}, 'intent': {'type': 'string', 'description': 'buy / consult / other'}}, required=['contact', 'question'])),
    Function(name='escalate_to_human', description='Передаёт менеджеру. Вызывай, если не знаешь ответа, клиент злится или вопрос не по базе.', parameters=FunctionParameters(type='object', properties={'reason': {'type': 'string'}}, required=['reason'])),
]


def knowledge():
    return path_from_base(settings.KNOWLEDGE_PATH).read_text(encoding='utf-8')[:100_000]


def search_knowledge(query):
    words = [word[:5] for word in re.findall(r'\w+', query.lower()) if len(word) >= 3]
    paragraphs = re.split(r'\n\s*\n', knowledge())
    # Sheet rows are separate searchable chunks and are refreshed at query time.
    live = gsheets.context()
    paragraphs = live.splitlines() + paragraphs
    ranked = sorted(((sum(5 for word in words if word in p.lower()), i, p) for i, p in enumerate(paragraphs)), key=lambda x: (-x[0], x[1]))
    matches = [p[:3000] for score, _, p in ranked if score > 0][:3]
    return {'status': 'success', 'results': matches} if matches else {'status': 'empty'}


def keyboard(tg_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📥 Ответить', callback_data=f'reply:{tg_id}')], [InlineKeyboardButton(text='⏹ Завершить', callback_data=f'end:{tg_id}')]])


async def escalate(bot, tg_id, username, reason):
    recent = repos.history(tg_id, 10)
    lines = '\n'.join(('Клиент: ' if role == 'user' else 'Администратор: ') + content[:300] for role, content in recent) or '— нет сообщений'
    label = f'@{username}' if username else 'без username'
    text = f'⚠️ Эскалация\nКлиент: {label} ({tg_id})\nПричина: {reason[:500]}\nКонтекст диалога (до 10 сообщений):\n{lines}'
    # Mark only after delivery, allowing a retry if Telegram rejects the message.
    await bot.send_message(settings.OWNER_TG_ID, text[:4000], reply_markup=keyboard(tg_id))
    repos.set_escalated(tg_id, True, reason)


async def save_lead(bot, tg_id, username, contact, question, intent='consult'):
    # Persist first; notification failure must not be reported as a failed save.
    await asyncio.to_thread(gsheets.save, tg_id, username, contact, question, intent)
    repos.add_lead(tg_id, username, contact, question, intent)
    label = f'@{username}' if username else str(tg_id)
    text = f'📋 Новая заявка\nКлиент: {label} ({tg_id})\nКонтакт: {contact[:250]}\nЗапрос: {question[:1000]}\nИнтерес: {intent[:50]}'
    try:
        await bot.send_message(settings.OWNER_TG_ID, text[:4000], reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text='📋 Открыть заявки', callback_data='admin:leads')]]))
    except Exception:
        import logging
        logging.exception('Lead was saved but owner notification failed')


def contact_was_shared(tg_id, contact):
    """Do not allow model-generated or guessed contact details into the CRM."""
    if not isinstance(contact, str) or not contact.strip() or len(contact) > 200:
        return False
    candidate = contact.strip().casefold()
    digits = re.sub(r'\D', '', candidate)
    for role, text in repos.history(tg_id, 10):
        if role != 'user':
            continue
        if candidate in text.casefold():
            return True
        if 10 <= len(digits) <= 15:
            for match in re.findall(r'\+?\d[\d ()\-]{8,}\d', text):
                if re.sub(r'\D', '', match) == digits:
                    return True
    return False


async def execute_tool(name, args, bot, tg_id, username):
    try:
        if name == 'search_knowledge':
            result = await asyncio.to_thread(search_knowledge, args['query'])
        elif name == 'save_lead':
            contact, question = args['contact'], args['question']
            intent = args.get('intent', 'other')
            if not contact_was_shared(tg_id, contact):
                result = {'status': 'error', 'message': 'Контакт не найден в сообщениях клиента. Не выдумывай его, попроси контакт или передай диалог менеджеру.'}
            else:
                await save_lead(bot, tg_id, username, contact, question, intent)
                result = {'status': 'success'}
        elif name == 'escalate_to_human':
            await escalate(bot, tg_id, username, args['reason'])
            result = {'status': 'success'}
        else:
            result = {'status': 'error', 'message': 'Unknown tool'}
    except Exception:
        import logging
        logging.exception('Tool failed: %s', name)
        if name in ('search_knowledge', 'save_lead', 'escalate_to_human'):
            raise
        result = {'status': 'error', 'message': 'Не удалось выполнить действие; передай запрос менеджеру, не утверждай, что действие выполнено.'}
    return json.dumps(result, ensure_ascii=False)
