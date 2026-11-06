"""
Plain sqlite3 (no ORM needed for a few small tables). One connection per
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
    label TEXT NOT NULL,
    commands_json TEXT NOT NULL,
    run_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    target TEXT  -- "inst:1" / "legacy:nqn"; NULL (older rows): whatever is active when it fires
);

-- Numbered orders sent to an installation (app/manager.py). v runs 1, 2, 3...
-- per installation with no gaps; the primary key makes a repeat impossible.
CREATE TABLE IF NOT EXISTS orders (
    inst INTEGER NOT NULL,
    v INTEGER NOT NULL,
    at_ms INTEGER NOT NULL,
    cmds_json TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (inst, v)
);

-- next_v: the version the next order to this installation gets.
CREATE TABLE IF NOT EXISTS installations (
    inst INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    next_v INTEGER NOT NULL DEFAULT 1
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


def _migrate_legacy_schedule_table(conn):
    """Schedules used to be one command_id/value_json per row. That shape can't
    represent a multi-command state trigger, so on upgrade we drop any rows in
    the old shape rather than shoehorn them in -- they're trivially re-created
    in the new schedule UI, and this only ever touches not-yet-fired rows."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(scheduled_commands)")}
    if cols and "label" not in cols:
        conn.execute("DROP TABLE scheduled_commands")


def _add_schedule_target_column(conn):
    """Schedules remember what they were created for since there's more than
    one kind of target. Rows from before that keep target NULL."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(scheduled_commands)")}
    if "target" not in cols:
        conn.execute("ALTER TABLE scheduled_commands ADD COLUMN target TEXT")


def init_db():
    with get_conn() as conn:
        _migrate_legacy_schedule_table(conn)
        conn.executescript(SCHEMA)
        _add_schedule_target_column(conn)


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


def insert_schedule(label: str, commands_json: str, run_at: float, target: str | None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO scheduled_commands (label, commands_json, run_at, created_at, target) "
            "VALUES (?, ?, ?, ?, ?)",
            (label, commands_json, run_at, time.time(), target),
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


def ensure_installation(inst: int, label: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO installations (inst, label, next_v) VALUES (?, ?, 1) "
            "ON CONFLICT(inst) DO UPDATE SET label = excluded.label",
            (inst, label),
        )


def get_next_versions() -> dict[int, int]:
    """next_v per installation: the stored counter, but never below one past
    the highest order actually stored."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT i.inst, MAX(i.next_v, COALESCE((SELECT MAX(v) FROM orders o WHERE o.inst = i.inst), 0) + 1) "
            "AS next_v FROM installations i"
        ).fetchall()
    return {r["inst"]: r["next_v"] for r in rows}


def set_next_version(inst: int, next_v: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE installations SET next_v = MAX(next_v, ?) WHERE inst = ?",
            (next_v, inst),
        )


def insert_order(inst: int, v: int, at_ms: int, cmds_json: str, source: str):
    """Stores a published order and moves the installation's counter past it,
    in one transaction."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO orders (inst, v, at_ms, cmds_json, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (inst, v, at_ms, cmds_json, source, time.time()),
        )
        conn.execute(
            "UPDATE installations SET next_v = MAX(next_v, ?) WHERE inst = ?",
            (v + 1, inst),
        )


def list_orders(inst: int) -> list[dict]:
    """Every stored order for one installation, oldest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE inst = ? ORDER BY v", (inst,)
        ).fetchall()
    return [dict(r) for r in rows]
