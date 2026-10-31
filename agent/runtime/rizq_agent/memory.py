"""Local chat history for the cashier assistant; not an authentication system."""
from __future__ import annotations
import os
from pathlib import Path
import sqlite3

PATH = Path(os.environ.get('RIZQ_MEMORY_DB', str(Path(__file__).resolve().parents[1] / 'data' / 'chat_memory.sqlite')))


_CONV = "COALESCE(conversation, 'day-' || date(created_at, 'localtime'))"


def connect():
    PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = sqlite3.connect(str(PATH), timeout=10)
    os.chmod(PATH, 0o600)
    db.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    db.execute('CREATE INDEX IF NOT EXISTS messages_user_id ON messages(user_id, id)')
    # Conversations ("sessions"): older rows have none and are grouped by their local day.
    if 'conversation' not in {r[1] for r in db.execute('PRAGMA table_info(messages)')}:
        db.execute('ALTER TABLE messages ADD COLUMN conversation TEXT')
    return db


def _latest(db, user_id: str) -> str | None:
    row = db.execute(f'SELECT {_CONV} FROM messages WHERE user_id=? ORDER BY id DESC LIMIT 1', (user_id,)).fetchone()
    return row[0] if row else None


def recent(user_id: str, limit: int = 30, conversation: str | None = None) -> list[dict]:
    with connect() as db:
        conv = conversation or _latest(db, user_id)
        rows = db.execute(f'SELECT role,text FROM messages WHERE user_id=? AND {_CONV}=? ORDER BY id DESC LIMIT ?',
                          (user_id, conv, min(max(limit, 1), 100))).fetchall()
    return [{'role': role, 'text': text} for role, text in reversed(rows)]


def history(user_id: str, conversation: str | None = None) -> list[dict]:
    """One conversation: the one asked for, or the most recent."""
    with connect() as db:
        conv = conversation or _latest(db, user_id)
        rows = db.execute(f'SELECT role,text FROM messages WHERE user_id=? AND {_CONV}=? ORDER BY id', (user_id, conv)).fetchall()
    return [{'role': role, 'text': text} for role, text in rows]


def conversations(user_id: str, limit: int = 60) -> list[dict]:
    """The caller's conversations, newest first, with local day and time."""
    with connect() as db:
        rows = db.execute(f"""SELECT {_CONV} AS conv, MIN(datetime(created_at,'localtime')), MAX(datetime(created_at,'localtime')),
                                     COUNT(*), MAX(id)
                              FROM messages WHERE user_id=? GROUP BY conv ORDER BY MAX(id) DESC LIMIT ?""",
                           (user_id, limit)).fetchall()
        out = []
        for conv, first, last, count, _ in rows:
            title = db.execute(f"SELECT text FROM messages WHERE user_id=? AND {_CONV}=? AND role='user' ORDER BY id LIMIT 1",
                               (user_id, conv)).fetchone()
            out.append({'id': conv, 'day': first[:10], 'started': first[11:16], 'last': last[11:16],
                        'messages': count, 'title': (title[0] if title else '')[:60]})
    return out


def append_exchange(user_id: str, question: str, answer: str, conversation: str | None = None) -> None:
    with connect() as db:
        db.executemany('INSERT INTO messages(user_id,role,text,conversation) VALUES (?,?,?,?)',
                       [(user_id, 'user', question, conversation), (user_id, 'assistant', answer, conversation)])
