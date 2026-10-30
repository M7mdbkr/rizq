"""Local chat history for the cashier assistant; not an authentication system."""
from __future__ import annotations
import os
from pathlib import Path
import sqlite3

PATH = Path(os.environ.get('RIZQ_MEMORY_DB', str(Path(__file__).resolve().parents[1] / 'data' / 'chat_memory.sqlite')))


def connect():
    PATH.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    db = sqlite3.connect(str(PATH), timeout=10)
    os.chmod(PATH, 0o600)
    db.execute('CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    db.execute('CREATE INDEX IF NOT EXISTS messages_user_id ON messages(user_id, id)')
    return db


def recent(user_id: str, limit: int = 30) -> list[dict]:
    with connect() as db:
        rows = db.execute('SELECT role,text FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?', (user_id, min(max(limit, 1), 100))).fetchall()
    return [{'role': role, 'text': text} for role, text in reversed(rows)]


def history(user_id: str) -> list[dict]:
    with connect() as db:
        rows = db.execute('SELECT role,text FROM messages WHERE user_id=? ORDER BY id', (user_id,)).fetchall()
    return [{'role': role, 'text': text} for role, text in rows]


def append_exchange(user_id: str, question: str, answer: str) -> None:
    with connect() as db:
        db.executemany('INSERT INTO messages(user_id,role,text) VALUES (?,?,?)', [(user_id, 'user', question), (user_id, 'assistant', answer)])
