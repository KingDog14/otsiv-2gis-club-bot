import asyncio
import json
from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole
from config import settings
from core.tools import functions, knowledge, execute_tool

PROMPT = '''Ты — вежливый администратор {company}. Отвечаешь клиентам в Telegram.

БАЗА ЗНАНИЙ:
{context}

ПРАВИЛА:
1. Отвечай ТОЛЬКО по базе знаний. Нет ответа → escalate_to_human.
2. Коротко: 2–4 предложения.
3. Оставил контакт или готов купить → save_lead. Если контакта нет, попроси его.
4. Не выдумывай цены/сроки/условия.
5. Не представляйся как ИИ или GigaChat. Ты — администратор.
6. Не утверждай, что лид сохранён или запрос передан, если инструмент вернул ошибку.
'''


def build_client():
    return GigaChat(base_url=settings.GIGACHAT_BASE_URL, credentials=settings.GIGACHAT_CREDENTIALS, scope='GIGACHAT_API_PERS', model='GigaChat', verify_ssl_certs=False, timeout=60)


def chat(payload):
    with build_client() as client:
        return client.chat(payload)


async def prepare(bot, tg_id, username, history, text):
    messages = [Messages(role=MessagesRole.SYSTEM, content=PROMPT.format(company=settings.COMPANY_NAME, context=knowledge()))]
    messages += [Messages(role=MessagesRole.USER if role == 'user' else MessagesRole.ASSISTANT, content=content) for role, content in history]
    messages.append(Messages(role=MessagesRole.USER, content=text))
    for _ in range(4):
        payload = Chat(messages=messages, functions=functions, function_call='auto')
        response = await asyncio.to_thread(chat, payload)
        choice = response.choices[0]
        if choice.finish_reason == 'function_call' and choice.message.function_call:
            call = choice.message.function_call
            args = json.loads(call.arguments) if isinstance(call.arguments, str) else call.arguments
            result = await execute_tool(call.name, args, bot, tg_id, username)
            messages.append(choice.message)
            messages.append(Messages(role=MessagesRole.FUNCTION, content=result, name=call.name))
        elif choice.finish_reason == 'stop':
            answer = choice.message.content or ''
            if not answer.strip():
                raise RuntimeError('Пустой ответ GigaChat')
            return answer
        else:
            raise RuntimeError(f'Неожиданный finish_reason: {choice.finish_reason}')
    raise RuntimeError('Превышен лимит вызовов инструментов')

