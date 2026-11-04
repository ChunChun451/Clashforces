"""Upsolve list upkeep.

Unsolved match problems are added at finalisation. They are marked done when
an OK is seen: by the poller during later matches, by an on-demand refresh
from the upsolve page, or by a slow background sweep using spare API slots.
"""

import asyncio
import json
import logging

from app import clock, config, db
from app.cf.client import CFError, Lane
from app.cf.parse import problem_url
from app.poller import history

log = logging.getLogger("upsolve")

_last_refresh: dict[int, int] = {}


def items(user_id: int) -> list[dict]:
    rows = db.all(
        "SELECT u.problem_key, u.added_at, u.done_at, m.code, p.name, p.rating, p.tags, p.contest_id, p.idx"
        " FROM upsolve u JOIN matches m ON m.id = u.match_id LEFT JOIN problems p ON p.key = u.problem_key"
        " WHERE u.user_id = ? ORDER BY u.done_at IS NOT NULL, u.added_at DESC", (user_id,))
    return [{
        "key": r["problem_key"], "name": r["name"] or r["problem_key"], "rating": r["rating"],
        "tags": json.loads(r["tags"]) if r["tags"] else [], "match_code": r["code"],
        "added_at": r["added_at"], "done_at": r["done_at"],
        "url": problem_url(r["contest_id"], r["idx"]),
    } for r in rows]


def can_refresh(user_id: int) -> bool:
    return clock.now() - _last_refresh.get(user_id, -10**9) >= config.UPSOLVE_REFRESH_COOLDOWN_S


async def refresh(user_id: int, lane: Lane = Lane.INTERACTIVE) -> bool:
    if not can_refresh(user_id):
        return False
    _last_refresh[user_id] = clock.now()
    await history.sync(user_id, lane)
    return True


async def sweep_loop() -> None:
    while True:
        try:
            cutoff = clock.now() - config.UPSOLVE_SWEEP_EVERY_S
            due = [r["user_id"] for r in db.all(
                "SELECT DISTINCT user_id FROM upsolve WHERE done_at IS NULL"
                " AND user_id NOT IN (SELECT user_id FROM active_locks)")
                if _last_refresh.get(r["user_id"], 0) < cutoff]
            for uid in due[:5]:
                _last_refresh[uid] = clock.now()
                await history.sync(uid, Lane.BACKGROUND)
        except CFError as e:
            log.warning("upsolve sweep: %s", e)
        except Exception:
            log.exception("upsolve sweep failed")
        await asyncio.sleep(60)
