"""Problem and contest cache in SQLite.

problemset.problems refreshes every 24h, contest.list every hour (it decides
which contests are running right now). An in-memory copy of the problem list
is kept for fast selection and rebuilt whenever the tables change.
"""

import asyncio
import json
import logging
import time

from app import config, db
from app.cf.client import CFClient, CFError, Lane
from app.cf.parse import GYM_MIN_ID, is_gym, parse_contests, parse_problems
from app.core.dedup import dup_groups
from app.models import Contest, Problem

log = logging.getLogger("cache")

_problems: list[Problem] | None = None
_by_key: dict[str, Problem] = {}


def _invalidate() -> None:
    global _problems, _by_key
    _problems, _by_key = None, {}


def store_contests(contests: list[Contest]) -> None:
    with db.transaction() as c:
        c.execute("DELETE FROM contests")
        c.executemany(
            "INSERT INTO contests(id, name, phase, start_time, duration) VALUES (?,?,?,?,?)",
            [(x.id, x.name, x.phase, x.start_time, x.duration) for x in contests],
        )
    db.kv_set("contests_refreshed_at", int(time.time()))
    _invalidate()


def store_problems(problems: list[Problem]) -> None:
    groups = dup_groups(problems, load_contests())
    with db.transaction() as c:
        c.execute("DELETE FROM problems WHERE contest_id < ?", (GYM_MIN_ID,))  # gym rows come from hosts
        c.executemany(
            "INSERT INTO problems(key, contest_id, idx, name, rating, tags, dup_group) VALUES (?,?,?,?,?,?,?)",
            [(p.key, p.contest_id, p.index, p.name, p.rating, json.dumps(list(p.tags)), groups[p.key])
             for p in problems],
        )
    db.kv_set("problemset_refreshed_at", int(time.time()))
    _invalidate()


def load_contests() -> list[Contest]:
    return [Contest(r["id"], r["name"], r["phase"], r["start_time"], r["duration"])
            for r in db.all("SELECT * FROM contests")]


def running_contest_ids() -> set[int]:
    return {r["id"] for r in db.all("SELECT id FROM contests WHERE phase NOT IN ('FINISHED', 'BEFORE')")}


def problems() -> list[Problem]:
    global _problems, _by_key
    if _problems is None:
        _problems = [
            Problem(r["key"], r["contest_id"], r["idx"], r["name"], r["rating"],
                    tuple(json.loads(r["tags"])), r["dup_group"])
            for r in db.all("SELECT * FROM problems")
        ]
        _by_key = {p.key: p for p in _problems}
    return _problems


def add_gym(problems: list[Problem]) -> None:
    """Remember gym/mashup problems a host listed. They have no rating or tags, and their
    name is empty until a player's submission tells us (see learn_gym_names)."""
    rows = [(p.key, p.contest_id, p.index, p.name, None, "[]", p.key) for p in problems if is_gym(p.contest_id)]
    if not rows:
        return
    db.executemany("INSERT OR IGNORE INTO problems(key, contest_id, idx, name, rating, tags, dup_group)"
                   " VALUES (?,?,?,?,?,?,?)", rows)
    _invalidate()


def learn_gym_names(subs) -> None:
    """Fill in gym problem names from submissions (the API can't list a gym's problems without a key)."""
    unnamed = {r["key"] for r in db.all("SELECT key FROM problems WHERE contest_id >= ? AND name = ''", (GYM_MIN_ID,))}
    named = {s.problem_key: s.problem_name for s in subs if s.problem_name and s.problem_key in unnamed}
    for key, name in named.items():
        db.execute("UPDATE problems SET name = ? WHERE key = ? AND name = ''", (name, key))
    if named:
        _invalidate()


def get(key: str) -> Problem | None:
    problems()
    return _by_key.get(key)


def all_tags() -> list[str]:
    return sorted({t for p in problems() for t in p.tags})


def contest_start(contest_id: int) -> int | None:
    return db.scalar("SELECT start_time FROM contests WHERE id = ?", (contest_id,))


def age_s(key: str) -> float:
    at = db.kv_get(key)
    return float("inf") if at is None else time.time() - at


async def refresh_contests(cf: CFClient, lane: Lane = Lane.BACKGROUND) -> None:
    result = await cf.call("contest.list", {"gym": "false"}, lane=lane, timeout=config.CF_LONG_TIMEOUT_S)
    store_contests(parse_contests(result))


async def refresh_problems(cf: CFClient, lane: Lane = Lane.BACKGROUND) -> None:
    result = await cf.call("problemset.problems", lane=lane, timeout=config.CF_LONG_TIMEOUT_S)
    store_problems(parse_problems(result))


async def refresh_loop(cf: CFClient) -> None:
    """Keep both caches fresh. An empty or very stale cache is refreshed urgently."""
    while True:
        try:
            if age_s("contests_refreshed_at") >= config.CONTEST_LIST_REFRESH_S:
                lane = Lane.INTERACTIVE if age_s("contests_refreshed_at") == float("inf") else Lane.BACKGROUND
                await refresh_contests(cf, lane)
            p_age = age_s("problemset_refreshed_at")
            if p_age >= config.PROBLEMSET_REFRESH_S:
                lane = Lane.INTERACTIVE if p_age >= config.PROBLEMSET_URGENT_AFTER_S else Lane.BACKGROUND
                await refresh_problems(cf, lane)
        except CFError as e:
            log.warning("cache refresh failed: %s", e)
        await asyncio.sleep(60)
