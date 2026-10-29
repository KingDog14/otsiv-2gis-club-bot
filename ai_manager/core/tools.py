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
    ranked = sorted(((sum(5 for word in words if word in p.lower()), i, p) for i, p in enumerate(paragraphs)), key=lambda x: (-x[0], x[1]))
    matches = [p[:3000] for score, _, p in ranked if score > 0][:3]
    return {'status': 'success', 'results': matches} if matches else {'status': 'empty'}


def keyboard(tg_id):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📥 Ответить', callback_data=f'reply:{tg_id}')], [InlineKeyboardButton(text='⏹ Завершить', callback_data=f'end:{tg_id}')]])


async def escalate(bot, tg_id, username, reason):
    recent = repos.history(tg_id, 3)
    lines = '\n'.join('— ' + content[:1000] for _, content in recent) or '— нет сообщений'
    label = f'@{username}' if username else 'без username'
    text = f'⚠️ Эскалация\nКлиент: {label} ({tg_id})\nПричина: {reason[:500]}\nПоследние 3 сообщения:\n{lines}'
    # Mark only after delivery, allowing a retry if Telegram rejects the message.
    await bot.send_message(settings.OWNER_TG_ID, text[:4000], reply_markup=keyboard(tg_id))
    repos.set_escalated(tg_id, True, reason)


async def execute_tool(name, args, bot, tg_id, username):
    try:
        if name == 'search_knowledge':
            result = await asyncio.to_thread(search_knowledge, args['query'])
        elif name == 'save_lead':
            contact, question = args['contact'], args['question']
            intent = args.get('intent', 'other')
            await asyncio.to_thread(gsheets.save, tg_id, username, contact, question, intent)
            repos.add_lead(tg_id, username, contact, question, intent)
            result = {'status': 'success'}
        elif name == 'escalate_to_human':
            await escalate(bot, tg_id, username, args['reason'])
            result = {'status': 'success'}
        else:
            result = {'status': 'error', 'message': 'Unknown tool'}
    except Exception:
        import logging
        logging.exception('Tool failed: %s', name)
        result = {'status': 'error', 'message': 'Не удалось выполнить действие; передай запрос менеджеру, не утверждай, что действие выполнено.'}
    return json.dumps(result, ensure_ascii=False)
