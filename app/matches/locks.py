"""One active lobby, match, or queue entry per user.

The PRIMARY KEY on active_locks.user_id makes the rule atomic: a second
insert for the same user fails.
"""

import sqlite3

from app import clock, db


class LockError(Exception):
    pass


def acquire(user_id: int, kind: str, match_id: int | None = None) -> None:
    try:
        db.execute(
            "INSERT INTO active_locks(user_id, kind, match_id, created_at) VALUES (?,?,?,?)",
            (user_id, kind, match_id, clock.now()),
        )
    except sqlite3.IntegrityError:
        held = current(user_id)
        if held and held["kind"] == kind and held["match_id"] == match_id:
            return  # already holding this exact lock
        raise LockError(describe(held))


def release(user_id: int, match_id: int | None = None) -> None:
    if match_id is None:
        db.execute("DELETE FROM active_locks WHERE user_id = ?", (user_id,))
    else:
        db.execute("DELETE FROM active_locks WHERE user_id = ? AND match_id = ?", (user_id, match_id))


def release_match(match_id: int) -> None:
    db.execute("DELETE FROM active_locks WHERE match_id = ?", (match_id,))


def current(user_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT l.*, m.code FROM active_locks l LEFT JOIN matches m ON m.id = l.match_id WHERE l.user_id = ?",
        (user_id,),
    )


def describe(lock) -> str:
    if lock is None:
        return "You are already busy."
    if lock["kind"] == "queue":
        return "You are in the ranked queue. Leave it first."
    return f"You are already in match {lock['code']}. Finish or leave it first."
