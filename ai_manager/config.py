import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')


def path_from_base(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else BASE / path


@dataclass(frozen=True)
class Settings:
    BOT_TOKEN: str = os.getenv('BOT_TOKEN', '')
    OWNER_TG_ID: int = int(os.getenv('OWNER_TG_ID', '0'))
    GIGACHAT_BASE_URL: str = os.getenv('GIGACHAT_BASE_URL', 'https://api.giga.chat/v1')
    GIGACHAT_CREDENTIALS: str = os.getenv('GIGACHAT_CREDENTIALS', '')
    GIGACHAT_MODEL: str = os.getenv('GIGACHAT_MODEL', 'GigaChat-2').strip()
    GSHEETS_CREDENTIALS_JSON: str = os.getenv('GSHEETS_CREDENTIALS_JSON', './gsheets.json')
    GSHEETS_SPREADSHEET_ID: str = os.getenv('GSHEETS_SPREADSHEET_ID', '')
    COMPANY_NAME: str = os.getenv('COMPANY_NAME', 'Тестовая компания')
    KNOWLEDGE_PATH: str = os.getenv('KNOWLEDGE_PATH', './knowledge.txt')
    BOT_SETUP_AVATAR: bool = os.getenv('BOT_SETUP_AVATAR', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
    BOT_SETUP_PROFILE: bool = os.getenv('BOT_SETUP_PROFILE', 'true').strip().lower() in ('1', 'true', 'yes', 'on')
    DB_PATH: str = os.getenv('DB_PATH', './manager.sqlite3')

    def validate(self):
        required = ('BOT_TOKEN', 'OWNER_TG_ID', 'GIGACHAT_CREDENTIALS', 'GIGACHAT_MODEL', 'GSHEETS_CREDENTIALS_JSON', 'GSHEETS_SPREADSHEET_ID')
        missing = [key for key in required if not getattr(self, key)]
        if missing:
            raise ValueError('Заполните в .env: ' + ', '.join(missing))
        if not path_from_base(self.KNOWLEDGE_PATH).is_file():
            raise ValueError('Файл базы знаний не найден: ' + str(path_from_base(self.KNOWLEDGE_PATH)))
        if not path_from_base(self.GSHEETS_CREDENTIALS_JSON).is_file():
            raise ValueError('Файл сервисного аккаунта не найден: ' + str(path_from_base(self.GSHEETS_CREDENTIALS_JSON)))


settings = Settings()
