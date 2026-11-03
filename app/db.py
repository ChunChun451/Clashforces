"""SQLite access. One shared connection used from the event loop.

Queries here are tiny, so running them on the loop is fine. Autocommit is on;
use `transaction()` when several writes must succeed or fail together.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app import config

SCHEMA = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 1

_conn: sqlite3.Connection | None = None


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (or reopen) the shared connection and make sure the schema exists."""
    global _conn
    if _conn is not None:
        _conn.close()
    conn = sqlite3.connect(path or config.DB_PATH, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA.read_text())
    _migrate(conn)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    _conn = conn
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Small additive changes for databases created by older versions."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(match_players)")}
    if "team" not in cols:
        conn.execute("ALTER TABLE match_players ADD COLUMN team INTEGER")


def conn() -> sqlite3.Connection:
    if _conn is None:
        return connect()
    return _conn


def execute(sql: str, params=()) -> sqlite3.Cursor:
    return conn().execute(sql, params)


def executemany(sql: str, rows) -> sqlite3.Cursor:
    return conn().executemany(sql, rows)


def one(sql: str, params=()) -> sqlite3.Row | None:
    return conn().execute(sql, params).fetchone()


def all(sql: str, params=()) -> list[sqlite3.Row]:  # noqa: A001 - short name is clearer here
    return conn().execute(sql, params).fetchall()


def scalar(sql: str, params=()):
    row = one(sql, params)
    return None if row is None else row[0]


@contextmanager
def transaction():
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")


def kv_get(key: str, default=None):
    value = scalar("SELECT value FROM kv WHERE key = ?", (key,))
    return default if value is None else json.loads(value)


def kv_set(key: str, value) -> None:
    execute(
        "INSERT INTO kv(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )
