"""User rows: lookups and small updates."""

import sqlite3

from app import clock, config, db


def by_id(user_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM users WHERE id = ?", (user_id,))


def by_handle(handle: str) -> sqlite3.Row | None:
    return db.one("SELECT * FROM users WHERE handle = ?", (handle,))


def create(handle: str, cf_rating: int, cf_max_rating: int) -> int:
    start = cf_rating if cf_rating > 0 else config.UNRATED_START
    cur = db.execute(
        "INSERT INTO users(handle, cf_rating, cf_max_rating, duel_rating, created_at) VALUES (?,?,?,?,?)",
        (handle, cf_rating, cf_max_rating, start, clock.now()),
    )
    return cur.lastrowid


def release_handle_from_others(handle: str, keep_user_id: int | None) -> None:
    """Someone proved they own `handle`; any other account loses it and must re-verify."""
    db.execute(
        "UPDATE users SET handle = NULL, needs_reverify = 1 WHERE handle = ? AND id IS NOT ?",
        (handle, keep_user_id),
    )


def set_handle(user_id: int, handle: str, cf_rating: int, cf_max_rating: int) -> None:
    db.execute(
        "UPDATE users SET handle = ?, cf_rating = ?, cf_max_rating = ?, needs_reverify = 0 WHERE id = ?",
        (handle, cf_rating, cf_max_rating, user_id),
    )


def mark_needs_reverify(user_id: int) -> None:
    db.execute("UPDATE users SET needs_reverify = 1 WHERE id = ?", (user_id,))


def touch_seen(user_id: int) -> None:
    db.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (clock.now(), user_id))


def public(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "handle": row["handle"],
        "cf_rating": row["cf_rating"],
        "cf_max_rating": row["cf_max_rating"],
        "duel_rating": row["duel_rating"],
        "needs_reverify": bool(row["needs_reverify"]),
    }
