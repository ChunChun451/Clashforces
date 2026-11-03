"""Ranked queue: join/leave, the matchmaking worker, and Elo after the result."""

import asyncio
import logging

from app import clock, config, db
from app.auth import users
from app.core import elo, matchmaking
from app.matches import events, finalize, locks, service, store
from app.ws.hub import hub

log = logging.getLogger("queue")


class QueueError(Exception):
    pass


def join(user) -> None:
    if user["handle"] is None or user["needs_reverify"]:
        raise QueueError("Your Codeforces handle changed. Re-verify it in Settings first.")
    with db.transaction():
        try:
            locks.acquire(user["id"], "queue")
        except locks.LockError as e:
            raise QueueError(str(e))
        db.execute("INSERT OR IGNORE INTO queue(user_id, duel_rating, joined_at) VALUES (?,?,?)",
                   (user["id"], user["duel_rating"], clock.now()))


def leave(user_id: int) -> None:
    with db.transaction():
        db.execute("DELETE FROM queue WHERE user_id = ?", (user_id,))
        lock = locks.current(user_id)
        if lock and lock["kind"] == "queue":
            locks.release(user_id)


def status(user_id: int) -> dict:
    row = db.one("SELECT * FROM queue WHERE user_id = ?", (user_id,))
    if row is None:
        return {"in_queue": False, "players_waiting": db.scalar("SELECT COUNT(*) FROM queue")}
    waited = clock.now() - row["joined_at"]
    return {"in_queue": True, "waited_s": waited, "window": matchmaking.window(waited),
            "duel_rating": row["duel_rating"], "players_waiting": db.scalar("SELECT COUNT(*) FROM queue")}


def match_pairs() -> list[str]:
    """Pair waiting players and open a ranked lobby for each pair. Returns the new codes."""
    now = clock.now()
    entries = [matchmaking.Entry(r["user_id"], r["duel_rating"], r["joined_at"])
               for r in db.all("SELECT * FROM queue")]
    codes = []
    for a, b in matchmaking.pair(entries, now):
        ua, ub = users.by_id(a.user_id), users.by_id(b.user_id)
        lo, hi = matchmaking.ranked_range(ua["cf_rating"], ub["cf_rating"])
        s = {"count": config.RANKED_COUNT, "rating_min": lo, "rating_max": hi, "tags_include": [], "tags_avoid": [],
             "avoid_interactive": False, "custom_problems": None, "duration_min": config.RANKED_DURATION_MIN,
             "freeze_min": 0, "handicap": None, "exclude": [],
             "queue_joined": {str(a.user_id): a.joined_at, str(b.user_id): b.joined_at}}
        with db.transaction():
            db.execute("DELETE FROM queue WHERE user_id IN (?, ?)", (a.user_id, b.user_id))
            locks.release(a.user_id)
            locks.release(b.user_id)
        code = service._insert_match(a.user_id, "ranked", "icpc", s, extra_players=(b.user_id,))
        for uid in (a.user_id, b.user_id):
            hub.send_user(uid, "queue.matched", {"code": code})
        codes.append(code)
    return codes


def expire_ready_timeouts() -> None:
    """A ranked lobby not readied in time is cancelled; ready players go back to the queue."""
    cutoff = clock.now() - config.RANKED_READY_TIMEOUT_S
    for m in db.all("SELECT * FROM matches WHERE mode = 'ranked' AND status = 'lobby' AND created_at < ?", (cutoff,)):
        joined = store.settings(m).get("queue_joined", {})
        players = store.players(m["id"])
        with db.transaction():
            db.execute("UPDATE matches SET status = 'cancelled', end_reason = 'cancelled', ended_at = ? WHERE id = ?",
                       (clock.now(), m["id"]))
            locks.release_match(m["id"])
            for p in players:
                if p["ready"]:
                    locks.acquire(p["user_id"], "queue")
                    db.execute("INSERT OR IGNORE INTO queue(user_id, duel_rating, joined_at) VALUES (?,?,?)",
                               (p["user_id"], p["duel_rating"], joined.get(str(p["user_id"]), clock.now())))
        events.state(m["id"])
        for p in players:
            hub.send_user(p["user_id"], "queue.requeued" if p["ready"] else "queue.dropped", {"code": m["code"]})


def apply_elo(match, rows) -> None:
    """finalize hook: ranked matches change duel rating (runs inside the finalize transaction)."""
    if match["mode"] != "ranked" or len(rows) != 2:
        return
    a, b = sorted(rows, key=lambda r: r.player)
    ra = db.scalar("SELECT duel_rating FROM users WHERE id = ?", (a.player,))
    rb = db.scalar("SELECT duel_rating FROM users WHERE id = ?", (b.player,))
    score_a = 0.5 if a.place == b.place else (1.0 if a.place < b.place else 0.0)
    na, nb = elo.update(ra, rb, score_a)
    now = clock.now()
    for uid, before, after in ((a.player, ra, na), (b.player, rb, nb)):
        db.execute("UPDATE users SET duel_rating = ? WHERE id = ?", (after, uid))
        db.execute("UPDATE match_players SET rating_before = ?, rating_after = ? WHERE match_id = ? AND user_id = ?",
                   (before, after, match["id"], uid))
        db.execute("INSERT INTO rating_history(user_id, match_id, before, after, created_at) VALUES (?,?,?,?,?)",
                   (uid, match["id"], before, after, now))


def setup() -> None:
    if apply_elo not in finalize.before_broadcast:
        finalize.before_broadcast.append(apply_elo)


async def run() -> None:
    setup()
    tick = 0
    while True:
        try:
            match_pairs()
            expire_ready_timeouts()
            if tick % 5 == 0:
                for r in db.all("SELECT user_id FROM queue"):
                    hub.send_user(r["user_id"], "queue.status", status(r["user_id"]))
        except Exception:
            log.exception("queue tick failed")
        tick += 1
        await asyncio.sleep(1)

