import sqlite3
from contextlib import contextmanager
from config import settings, path_from_base
from db.models import SCHEMA


@contextmanager
def connect():
    conn = sqlite3.connect(path_from_base(settings.DB_PATH), timeout=30)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as db:
        db.executescript(SCHEMA)


def ensure_conversation(tg_id, username):
    with connect() as db:
        db.execute('INSERT INTO conversations(tg_id, username) VALUES (?, ?) ON CONFLICT(tg_id) DO UPDATE SET username=excluded.username', (tg_id, username))


def add_message(tg_id, role, content):
    with connect() as db:
        db.execute('INSERT INTO messages(tg_id, role, content) VALUES (?, ?, ?)', (tg_id, role, content))


def history(tg_id, limit=10):
    with connect() as db:
        rows = db.execute('SELECT role, content FROM messages WHERE tg_id=? ORDER BY id DESC LIMIT ?', (tg_id, limit)).fetchall()
    return list(reversed(rows))


def is_escalated(tg_id):
    with connect() as db:
        row = db.execute('SELECT escalated FROM conversations WHERE tg_id=?', (tg_id,)).fetchone()
    return bool(row and row[0])


def set_escalated(tg_id, value, reason=None):
    with connect() as db:
        db.execute('UPDATE conversations SET escalated=? WHERE tg_id=?', (int(value), tg_id))
        if value and reason is not None:
            db.execute('INSERT INTO escalations(tg_id, reason) VALUES (?, ?)', (tg_id, reason))


def add_lead(tg_id, username, contact, question, intent):
    with connect() as db:
        db.execute('INSERT INTO leads(tg_id, username, contact, question, intent) VALUES (?, ?, ?, ?, ?)', (tg_id, username, contact, question, intent))


def stats():
    with connect() as db:
        return tuple(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('conversations', 'leads', 'escalations'))


def recent_leads():
    with connect() as db:
        return db.execute('SELECT created_at, tg_id, username, contact, question, intent FROM leads ORDER BY id DESC LIMIT 10').fetchall()
