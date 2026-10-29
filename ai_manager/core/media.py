"""Extract a textual description/transcript from user media via GigaChat files API."""
import asyncio
import io
import logging
from gigachat.models import Chat, Messages, MessagesRole
from core.assistant import build_client
from config import settings

MAX_BYTES = 8 * 1024 * 1024


def describe(data, kind):
    filename = 'photo.jpg' if kind == 'photo' else 'voice.ogg'
    mime = 'image/jpeg' if kind == 'photo' else 'audio/x-ogg'
    prompt = ('Кратко опиши изображение и видимые надписи. Не делай выводов, которые нельзя проверить по изображению.'
              if kind == 'photo' else
              'Расшифруй речь на русском дословно. Не добавляй факты; неразборчивые фрагменты обозначь как неразборчивые.')
    with build_client() as client:
        file_id = None
        try:
            uploaded = client.upload_file((filename, data, mime), purpose='general')
            file_id = uploaded.id_
            response = client.chat(Chat(model=settings.GIGACHAT_MODEL, messages=[
                Messages(role=MessagesRole.USER, content=prompt, attachments=[file_id])]))
            text = response.choices[0].message.content or ''
            if not text.strip():
                raise ValueError('Empty media response')
            return text.strip()[:6000]
        finally:
            if file_id:
                try:
                    client.delete_file(file_id)
                except Exception:
                    logging.warning('Could not delete GigaChat uploaded file', exc_info=True)


async def extract(bot, telegram_file, kind):
    if not telegram_file.file_size or telegram_file.file_size > MAX_BYTES:
        raise ValueError('Media size is unknown or exceeds limit')
    file = await bot.get_file(telegram_file.file_id)
    if file.file_size and file.file_size > MAX_BYTES:
        raise ValueError('Media exceeds limit')
    output = io.BytesIO()
    await bot.download_file(file.file_path, destination=output)
    data = output.getvalue()
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Invalid media size')
    return await asyncio.to_thread(describe, data, kind)
