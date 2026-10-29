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
    GSHEETS_CREDENTIALS_JSON: str = os.getenv('GSHEETS_CREDENTIALS_JSON', './gsheets.json')
    GSHEETS_SPREADSHEET_ID: str = os.getenv('GSHEETS_SPREADSHEET_ID', '')
    COMPANY_NAME: str = os.getenv('COMPANY_NAME', 'Ваша компания')
    KNOWLEDGE_PATH: str = os.getenv('KNOWLEDGE_PATH', './knowledge.txt')
    DB_PATH: str = os.getenv('DB_PATH', './manager.sqlite3')

    def validate(self):
        required = ('BOT_TOKEN', 'OWNER_TG_ID', 'GIGACHAT_CREDENTIALS', 'GSHEETS_CREDENTIALS_JSON', 'GSHEETS_SPREADSHEET_ID')
        missing = [key for key in required if not getattr(self, key)]
        if missing:
            raise ValueError('Заполните в .env: ' + ', '.join(missing))


settings = Settings()
