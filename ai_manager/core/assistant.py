import asyncio
import json
import logging
import threading
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
            return messages, choice.message.content or ''
        else:
            raise RuntimeError(f'Неожиданный finish_reason: {choice.finish_reason}')
    raise RuntimeError('Превышен лимит вызовов инструментов')


async def stream_final(messages):
    # A second generation is necessary for streaming; disable functions so it cannot
    # emit another tool call. If it fails, the caller uses the already checked answer.
    payload = Chat(messages=messages, function_call='none')
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def worker():
        try:
            with build_client() as client:
                for chunk in client.stream(payload):
                    text = getattr(getattr(chunk.choices[0], 'delta', None), 'content', None) if chunk.choices else None
                    if text:
                        loop.call_soon_threadsafe(queue.put_nowait, ('chunk', text))
        except Exception as exc:
            logging.exception('GigaChat streaming failed')
            loop.call_soon_threadsafe(queue.put_nowait, ('error', exc))
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, ('done', None))

    threading.Thread(target=worker, daemon=True).start()
    while True:
        kind, value = await queue.get()
        if kind == 'done':
            break
        if kind == 'error':
            raise value
        yield value
