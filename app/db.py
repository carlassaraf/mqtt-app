"""
Plain sqlite3 (no ORM needed for two small tables). One connection per
call is fine at this scale and keeps things thread-safe without extra care.
"""
import sqlite3
import time
from contextlib import contextmanager

from app.config import DB_PATH

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL,
    args_json TEXT NOT NULL,
    run_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def insert_log(topic: str, payload: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO logs (topic, payload, ts) VALUES (?, ?, ?)",
            (topic, payload, time.time()),
        )


def get_recent_logs(limit: int = 200):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def insert_schedule(command_id: str, args_json: str, run_at: float) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scheduled_commands (command_id, args_json, run_at, created_at) "
            "VALUES (?, ?, ?, ?)",
            (command_id, args_json, run_at, time.time()),
        )
        return cur.lastrowid


def list_schedules():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_commands WHERE status = 'pending' ORDER BY run_at"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_schedule(schedule_id: int, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE scheduled_commands SET status = ? WHERE id = ?",
            (status, schedule_id),
        )


def delete_schedule(schedule_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM scheduled_commands WHERE id = ?", (schedule_id,))
