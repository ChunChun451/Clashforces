"""Handle verification by compilation error.

The server picks a random easy problem. The user submits code that fails to
compile within VERIFY_WINDOW_S. We look for a COMPILATION_ERROR on that problem
with issued_at <= creationTimeSeconds <= expires_at (Codeforces time).
"""

import random
import re
import secrets

from app import clock, config, db
from app.auth import users
from app.cf import cache
from app.cf.client import CFError, HandleNotFound, Lane, get_client
from app.cf.parse import parse_submissions
from app.matches import locks
from app.models import COMPILATION_ERROR, Submission

HANDLE_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,24}$")


class VerifyError(Exception):
    pass


BANNED = "This account has been banned."


def found_ce(subs: list[Submission], problem_key: str, issued_at: int, expires_at: int) -> bool:
    return any(
        s.problem_key == problem_key and s.verdict == COMPILATION_ERROR and issued_at <= s.time <= expires_at
        for s in subs
    )


def pick_problem(rng: random.Random | None = None) -> str:
    running = cache.running_contest_ids()
    pool = [
        p.key for p in cache.problems()
        if p.rating == config.VERIFY_PROBLEM_RATING
        and "*special" not in p.tags and "interactive" not in p.tags
        and p.contest_id not in running
    ]
    if not pool:
        raise VerifyError("The problem list is still loading. Try again in a minute.")
    return (rng or random).choice(pool)


async def start(handle: str, user_id: int | None = None) -> dict:
    handle = handle.strip()
    if not HANDLE_RE.match(handle):
        raise VerifyError("That doesn't look like a Codeforces handle.")
    known = users.by_handle(handle)
    if known is not None and known["banned_at"]:
        raise VerifyError(BANNED)
    if user_id is not None and locks.current(user_id):
        raise VerifyError("You can't change your handle while in a match or queue.")
    try:
        info = await get_client().call("user.info", {"handles": handle}, lane=Lane.INTERACTIVE)
    except HandleNotFound:
        raise VerifyError(f"Codeforces has no user named {handle}.")
    except CFError:
        raise VerifyError("Codeforces is not answering right now. Try again soon.")
    info = info[0]
    task_id = secrets.token_urlsafe(16)
    problem_key = pick_problem()
    now = clock.now()
    db.execute(
        "INSERT INTO verifications(id, handle, user_id, problem_key, issued_at, expires_at, cf_rating, cf_max_rating, status)"
        " VALUES (?,?,?,?,?,?,?,?, 'pending')",
        (task_id, info["handle"], user_id, problem_key, now, now + config.VERIFY_WINDOW_S,
         info.get("rating", 0), info.get("maxRating", 0)),
    )
    return task_view(db.one("SELECT * FROM verifications WHERE id = ?", (task_id,)))


def task_view(task) -> dict:
    p = cache.get(task["problem_key"])
    return {
        "task_id": task["id"],
        "handle": task["handle"],
        "status": task["status"],
        "issued_at": task["issued_at"],
        "expires_at": task["expires_at"],
        "problem": {
            "key": task["problem_key"],
            "name": p.name if p else task["problem_key"],
            "url": f"https://codeforces.com/contest/{p.contest_id}/problem/{p.index}" if p else None,
        },
    }


async def check(task_id: str) -> tuple[dict, int | None]:
    """Check a task. Returns (view, user_id) where user_id is set once verified."""
    task = db.one("SELECT * FROM verifications WHERE id = ?", (task_id,))
    if task is None:
        raise VerifyError("Unknown verification task.")
    if task["status"] != "pending":
        return task_view(task), None
    now = clock.now()
    if now > task["expires_at"] + config.VERIFY_GRACE_S:
        db.execute("UPDATE verifications SET status = 'expired' WHERE id = ?", (task_id,))
        return task_view(db.one("SELECT * FROM verifications WHERE id = ?", (task_id,))), None
    last = task["last_checked_at"] or 0
    if now - last < config.VERIFY_CHECK_EVERY_S:
        return task_view(task), None
    db.execute("UPDATE verifications SET last_checked_at = ? WHERE id = ?", (now, task_id))
    try:
        raw = await get_client().call(
            "user.status", {"handle": task["handle"], "from": 1, "count": 10}, lane=Lane.INTERACTIVE
        )
    except CFError:
        return task_view(task), None
    subs = parse_submissions(raw, task["handle"])
    if not found_ce(subs, task["problem_key"], task["issued_at"], task["expires_at"]):
        return task_view(task), None
    user_id = complete(task)
    return task_view(db.one("SELECT * FROM verifications WHERE id = ?", (task_id,))), user_id


def complete(task) -> int:
    """Mark verified and attach the handle to the right account."""
    handle = task["handle"]
    with db.transaction():
        db.execute("UPDATE verifications SET status = 'verified' WHERE id = ?", (task["id"],))
        if task["user_id"] is not None:  # handle change on an existing account
            user_id = task["user_id"]
            users.release_handle_from_others(handle, user_id)
            users.set_handle(user_id, handle, task["cf_rating"], task["cf_max_rating"])
        else:
            existing = users.by_handle(handle)
            if existing:
                user_id = existing["id"]
                users.set_handle(user_id, handle, task["cf_rating"], task["cf_max_rating"])
            else:
                user_id = users.create(handle, task["cf_rating"], task["cf_max_rating"])
    return user_id
