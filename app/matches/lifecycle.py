"""The 1-second match ticker and reactions to new submissions.

- Ends each player's window on time (our clock), even if Codeforces is down.
- Final check per player at end_time + FINAL_CHECK_DELAY_S; retried until it
  succeeds and nothing in the window is TESTING. Until then the result is "pending".
- Lockout: ends early once the winner can't change (after a confirmation check).
- ICPC: ends early when every player has solved every problem.
"""

import asyncio
import logging

from app import clock, config, db
from app.cf.client import CFError, get_client
from app.core import lockout
from app.matches import events, finalize, locks, store, view
from app.models import Submission
from app.poller.poller import poller

log = logging.getLogger("lifecycle")

_sent: set = set()                      # one-time notifications already sent
_final_next: dict[tuple, int] = {}      # (match_id, user_id) -> next final-check attempt
_final_busy: set[tuple] = set()
_clinch_busy: set[int] = set()


def end_match(match_id: int, reason: str, at: int) -> None:
    """Close every window at `at` (or keep earlier per-player ends) and mark the match ended."""
    cur = db.execute(
        "UPDATE matches SET status = 'ended', ended_at = ?, end_reason = ?, result_status = 'pending'"
        " WHERE id = ? AND status = 'running'", (at, reason, match_id))
    if cur.rowcount != 1:
        return
    if reason != "time":
        db.execute("UPDATE match_players SET end_time = MIN(end_time, ?) WHERE match_id = ?",
                   (max(at, store.match_by_id(match_id)["start_time"]), match_id))
    for p in store.players(match_id):
        poller.unwatch(p["user_id"])
    locks.release_match(match_id)
    events.ended(match_id)


def _spawn(coro) -> None:
    asyncio.get_running_loop().create_task(coro)


# ---------- reacting to submissions ----------

def on_submissions(user_id: int, changed: list[Submission]) -> None:
    match = db.one(
        "SELECT m.* FROM matches m JOIN match_players mp ON mp.match_id = m.id WHERE mp.user_id = ?"
        " AND (m.status = 'running' OR (m.status = 'ended' AND m.result_status = 'pending'))"
        " ORDER BY m.id DESC LIMIT 1", (user_id,))
    if match is None:
        return
    events.submissions(match["id"], user_id, changed)
    if match["status"] != "running":
        return
    if match["rules"] == "lockout":
        if match["id"] not in _clinch_busy:
            _clinch_busy.add(match["id"])
            try:
                _spawn(check_clinch(match["id"]))
            except RuntimeError:
                _clinch_busy.discard(match["id"])
    else:
        check_all_solved(match)


def check_all_solved(match) -> None:
    rows, _ = store.standings(match)
    pb = store.problems(match["id"])
    team = store.is_team(match)
    members = store.team_of(store.players(match["id"])) if team else {}
    for row in rows:
        if all(c.solved for c in row.cells.values()) and row.cells:
            # nothing left to track for this player (or every member of this team)
            for uid in ([u for u, t in members.items() if t == row.player] if team else [row.player]):
                poller.unwatch(uid)
    active = [r for r in rows if not r.forfeited]
    need = (lambda r: len(store.player_problem_keys(pb, -1))) if team else \
        (lambda r: len(store.player_problem_keys(pb, r.player)))
    if active and all(need(r) == r.solved for r in active):
        last_ok = max(r.last_ok or 0 for r in active)
        end_match(match["id"], "all_solved", at=last_ok + 1)


async def check_clinch(match_id: int) -> None:
    try:
        match = store.match_by_id(match_id)
        if match is None or match["status"] != "running":
            return
        if not _decided(match):
            return
        if get_client().down:
            return  # never end early on stale data
        t0 = clock.now()
        for p in store.players(match_id):
            ok = await poller.final_check(p["user_id"], p["handle"], match["start_time"], min(p["end_time"], t0 + 1))
            if not ok:
                return  # something is still being judged; the next change re-triggers this
        match = store.match_by_id(match_id)
        if match["status"] == "running" and _decided(match, now=t0):
            end_match(match_id, "clinched", at=t0 + 1)
            db.execute("UPDATE match_players SET final_checked = 1 WHERE match_id = ?", (match_id,))
            finalize.finalize(match_id)
    except CFError as e:
        log.warning("clinch confirmation failed: %s", e)
    finally:
        _clinch_busy.discard(match_id)


def _decided(match, now: int | None = None) -> bool:
    now = now or clock.now()
    rows, _ = store.standings(match)
    pts = store.points(store.problems(match["id"]))
    players = store.players(match["id"])
    can_score = {p["user_id"] for p in players if p["forfeited_at"] is None and p["end_time"] > now}
    if store.is_team(match):
        # a team can still score while any member can
        members = store.team_of(players)
        can_score = {members[u] for u in can_score if u in members}
    return lockout.decided(rows, pts, can_score)


# ---------- ticker ----------

async def tick() -> None:
    now = clock.now()

    for m in db.all("SELECT id, code FROM matches WHERE status = 'lobby' AND created_at < ?",
                    (now - config.LOBBY_IDLE_S,)):
        db.execute("UPDATE matches SET status = 'cancelled', end_reason = 'cancelled', ended_at = ? WHERE id = ?",
                   (now, m["id"]))
        locks.release_match(m["id"])
        events.state(m["id"])

    for match in db.all("SELECT * FROM matches WHERE status = 'running'"):
        _tick_running(match, now)

    for match in db.all("SELECT * FROM matches WHERE status IN ('running', 'ended') AND result_status != 'final'"
                        " AND start_time IS NOT NULL"):
        _tick_final_checks(match, now)


def _once(key) -> bool:
    if key in _sent:
        return False
    _sent.add(key)
    return True


def _tick_running(match, now: int) -> None:
    mid = match["id"]
    if now >= match["start_time"] and _once(("started", mid)):
        events.state(mid)
    s = store.settings(match)
    fs = view.freeze_start(match, s)
    if fs is not None and now >= fs and _once(("freeze", mid)):
        events.freeze_started(match)
        events.state(mid)
    players = store.players(mid)
    for p in players:
        if p["forfeited_at"] is not None:
            continue
        warn_at = p["end_time"] - config.WARNING_MINUTES_LEFT * 60
        if warn_at <= now < p["end_time"] and _once(("warn", mid, p["user_id"])):
            events.warning(match, p["user_id"], config.WARNING_MINUTES_LEFT)
        if now >= p["end_time"]:
            poller.unwatch(p["user_id"])
    if players and all(now >= p["end_time"] for p in players):
        end_match(mid, "time", at=max(p["end_time"] for p in players))


def _tick_final_checks(match, now: int) -> None:
    for p in store.players(match["id"]):
        key = (match["id"], p["user_id"])
        if p["final_checked"] or key in _final_busy:
            continue
        if now < p["end_time"] + config.FINAL_CHECK_DELAY_S or now < _final_next.get(key, 0):
            continue
        _final_busy.add(key)
        _spawn(_final(match["id"], p["user_id"], p["handle"], match["start_time"], p["end_time"]))
    m = store.match_by_id(match["id"])
    if m["status"] == "ended" and all(p["final_checked"] for p in store.players(match["id"])):
        finalize.finalize(match["id"])


async def _final(match_id: int, user_id: int, handle: str, start: int, end: int) -> None:
    key = (match_id, user_id)
    try:
        ok = await poller.final_check(user_id, handle, start, end)
        if not ok and clock.now() - end > config.TESTING_GIVE_UP_S:
            log.warning("giving up on TESTING verdicts for %s in match %s", handle, match_id)
            ok = True
        if ok:
            db.execute("UPDATE match_players SET final_checked = 1 WHERE match_id = ? AND user_id = ?",
                       (match_id, user_id))
            _final_next.pop(key, None)
        else:
            _final_next[key] = clock.now() + config.FINAL_RETRY_S
    except CFError as e:
        log.warning("final check for %s failed, will retry: %s", handle, e)
        _final_next[key] = clock.now() + config.FINALIZE_RETRY_S
    except Exception:
        log.exception("final check error")
        _final_next[key] = clock.now() + config.FINALIZE_RETRY_S
    finally:
        _final_busy.discard(key)


def recover() -> None:
    """After a restart: unfinished starts go back to the lobby; running matches are watched again."""
    db.execute("UPDATE matches SET status = 'lobby' WHERE status = 'starting'")
    now = clock.now()
    for match in db.all("SELECT * FROM matches WHERE status = 'running'"):
        for p in store.players(match["id"]):
            if p["end_time"] > now and p["forfeited_at"] is None:
                poller.watch(p["user_id"], p["handle"], match["start_time"], p["end_time"])


def setup() -> None:
    if on_submissions not in poller.listeners:
        poller.listeners.append(on_submissions)


async def run() -> None:
    setup()
    recover()
    while True:
        try:
            await tick()
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(1)
