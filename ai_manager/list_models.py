"""List available GigaChat models, optionally smoke-test the configured model."""
import argparse
import sys
from config import settings
from core.assistant import build_client
from gigachat.models import Chat, Messages, MessagesRole


def main(argv=None):
    parser = argparse.ArgumentParser(description='Диагностика моделей GigaChat')
    parser.add_argument('--check', action='store_true', help='Отправить тестовый запрос выбранной модели')
    args = parser.parse_args(argv)
    if not settings.GIGACHAT_CREDENTIALS:
        print('Не заполнен GIGACHAT_CREDENTIALS в .env или переменных среды.', file=sys.stderr)
        return 1
    try:
        with build_client() as client:
            models = client.get_models().data
            print(f'Выбранная модель: {settings.GIGACHAT_MODEL}')
            if not models:
                print('API вернул пустой список моделей.')
                return 1
            print('Доступные модели:')
            for model in models:
                print(model.id_)
            print('Для GIGACHAT_MODEL выберите подходящую чат-модель; Embeddings не подходит.')
            if args.check:
                payload = Chat(model=settings.GIGACHAT_MODEL, messages=[Messages(role=MessagesRole.USER, content='Привет!')])
                result = client.chat(payload)
                if not result.choices or not result.choices[0].message.content:
                    raise ValueError('Пустой ответ модели')
                print('Тестовый запрос выполнен успешно. Function calling отдельно не проверялся.')
    except Exception as exc:
        # Do not expose response headers or sensitive configuration.
        print(f'Ошибка GigaChat ({type(exc).__name__}). Проверьте модель и настройки подключения.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
