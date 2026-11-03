"""Owner-only views of the site (users, matches, live numbers) and the few actions admins may take."""

import asyncio
import logging

from app import clock, config, db
from app.auth import admin as admin_auth
from app.auth import users
from app.cf.client import get_client
from app.matches import events, locks, store
from app.matches import queue as ranked_queue
from app.matches import service
from app.poller.poller import poller
from app.ws.hub import hub

log = logging.getLogger("admin")

USER_SORTS = {
    # key: ORDER BY clause; every sort ends on id so pages never overlap
    "joined": "u.created_at DESC, u.id DESC",
    "seen": "u.last_seen_at IS NULL, u.last_seen_at DESC, u.id DESC",
    "matches": "played DESC, u.id DESC",
    "rating": "u.cf_rating DESC, u.id DESC",
    "duel": "u.duel_rating DESC, u.id DESC",
}
MATCH_STATES = ("lobby", "starting", "running", "ended", "cancelled")
MATCH_MODES = ("friend", "group", "team", "ranked", "contest")
CANCELLABLE = ("lobby", "running")


class AdminError(Exception):
    pass


def _page(page) -> int:
    try:
        return max(1, int(page))
    except (TypeError, ValueError):
        return 1


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ---------- numbers ----------

def stats() -> dict:
    now = clock.now()
    u = db.one(
        "SELECT COUNT(*) AS total, COALESCE(SUM(created_at >= ?), 0) AS new_24h, COALESCE(SUM(created_at >= ?), 0) AS new_7d,"
        " COALESCE(SUM(last_seen_at >= ?), 0) AS active, COALESCE(SUM(banned_at IS NOT NULL), 0) AS banned FROM users",
        (now - 86400, now - 7 * 86400, now - config.ADMIN_ACTIVE_WINDOW_S))
    by_state = {s: 0 for s in MATCH_STATES}
    for r in db.all("SELECT status, COUNT(*) AS n FROM matches GROUP BY status"):
        by_state[r["status"]] = r["n"]
    cf = get_client()
    return {
        "server_time": now,
        "users": {"total": u["total"], "new_24h": u["new_24h"], "new_7d": u["new_7d"],
                  "active_now": u["active"], "banned": u["banned"]},
        "matches": {"by_state": by_state,
                    "last_24h": db.scalar("SELECT COUNT(*) FROM matches WHERE created_at >= ?", (now - 86400,)),
                    "contests_upcoming": db.scalar(
                        "SELECT COUNT(*) FROM matches WHERE mode = 'contest' AND status = 'lobby'"),
                    "in_queue": db.scalar("SELECT COUNT(*) FROM queue")},
        "live": {"sockets": len(hub.conns), "users_online": len(hub.by_user), "watched_players": len(poller.watches)},
        "cf": {"status": "down" if cf.down else "ok", "lanes": cf.queue_depths()},
    }


# ---------- users ----------

_PLAYED = ("SELECT mp.user_id, COUNT(*) AS played, SUM(mp.place = 1) AS wins FROM match_players mp"
           " JOIN matches m ON m.id = mp.match_id WHERE m.result_status = 'final' GROUP BY mp.user_id")


def list_users(q: str = "", sort: str = "joined", page=1) -> dict:
    page = _page(page)
    order = USER_SORTS.get(sort, USER_SORTS["joined"])
    where, params = "", []
    q = (q or "").strip()[:32]
    if q:
        where = "WHERE u.handle LIKE ? ESCAPE '\\'"
        params.append(_like(q))
    total = db.scalar(f"SELECT COUNT(*) FROM users u {where}", params)
    rows = db.all(
        f"SELECT u.*, COALESCE(a.played, 0) AS played, COALESCE(a.wins, 0) AS wins"
        f" FROM users u LEFT JOIN ({_PLAYED}) a ON a.user_id = u.id {where}"
        f" ORDER BY {order} LIMIT ? OFFSET ?",
        (*params, config.ADMIN_PAGE_SIZE, (page - 1) * config.ADMIN_PAGE_SIZE))
    return {
        "total": total, "page": page, "page_size": config.ADMIN_PAGE_SIZE, "sort": sort if sort in USER_SORTS else "joined",
        "users": [{
            "id": r["id"], "handle": r["handle"], "cf_rating": r["cf_rating"], "cf_max_rating": r["cf_max_rating"],
            "duel_rating": r["duel_rating"], "created_at": r["created_at"], "last_seen_at": r["last_seen_at"],
            "banned_at": r["banned_at"], "needs_reverify": bool(r["needs_reverify"]),
            "played": r["played"], "wins": r["wins"], "online": hub.online(r["id"]),
            "is_admin": admin_auth.is_admin(r),
        } for r in rows],
    }


# ---------- matches ----------

def list_matches(state: str = "", mode: str = "", page=1) -> dict:
    page = _page(page)
    conds, params = [], []
    if state in MATCH_STATES:
        conds.append("m.status = ?")
        params.append(state)
    if mode in MATCH_MODES:
        conds.append("m.mode = ?")
        params.append(mode)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = db.scalar(f"SELECT COUNT(*) FROM matches m {where}", params)
    rows = db.all(
        "SELECT m.id, m.code, m.mode, m.rules, m.status, m.result_status, m.created_at, m.start_time, m.end_time,"
        " m.ended_at, m.end_reason, m.scheduled_at, json_extract(m.settings, '$.title') AS title,"
        " u.handle AS creator, u.cf_rating AS creator_rating,"
        " (SELECT COUNT(*) FROM match_players p WHERE p.match_id = m.id) AS players"
        f" FROM matches m LEFT JOIN users u ON u.id = m.creator_id {where}"
        " ORDER BY m.created_at DESC, m.id DESC LIMIT ? OFFSET ?",
        (*params, config.ADMIN_PAGE_SIZE, (page - 1) * config.ADMIN_PAGE_SIZE))
    return {"total": total, "page": page, "page_size": config.ADMIN_PAGE_SIZE,
            "server_time": clock.now(), "matches": [dict(r) for r in rows]}


# ---------- recent activity ----------

def activity(limit: int = 12) -> dict:
    items = []
    for r in db.all("SELECT handle, cf_rating, created_at FROM users ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)):
        items.append({"kind": "signup", "at": r["created_at"], "handle": r["handle"], "cf_rating": r["cf_rating"]})
    match_cols = ("m.code, m.mode, m.rules, m.status, m.created_at, m.ended_at, json_extract(m.settings, '$.title') AS title,"
                  " u.handle, u.cf_rating FROM matches m LEFT JOIN users u ON u.id = m.creator_id")
    for r in db.all(f"SELECT {match_cols} ORDER BY m.created_at DESC, m.id DESC LIMIT ?", (limit,)):
        items.append({"kind": "created", "at": r["created_at"], "code": r["code"], "mode": r["mode"], "rules": r["rules"],
                      "title": r["title"], "handle": r["handle"], "cf_rating": r["cf_rating"]})
    for r in db.all(f"SELECT {match_cols} WHERE m.ended_at IS NOT NULL ORDER BY m.ended_at DESC, m.id DESC LIMIT ?",
                    (limit,)):
        items.append({"kind": "cancelled" if r["status"] == "cancelled" else "finished", "at": r["ended_at"],
                      "code": r["code"], "mode": r["mode"], "rules": r["rules"], "title": r["title"]})
    items.sort(key=lambda i: -i["at"])
    return {"items": items[:limit]}


# ---------- actions ----------

def cancel_match(code: str) -> None:
    """Cancel a lobby or a running match, the way a host's cancel does: locks freed, viewers told."""
    match = store.match_by_code(code)
    if match is None:
        raise AdminError("No match with that code.")
    now = clock.now()
    cur = db.execute(
        "UPDATE matches SET status = 'cancelled', end_reason = 'cancelled', ended_at = ? WHERE id = ? AND status IN (?, ?)",
        (now, match["id"], *CANCELLABLE))
    if cur.rowcount != 1:
        raise AdminError("Only a lobby or a running match can be cancelled.")
    locks.release_match(match["id"])
    if match["status"] == "running":
        for p in store.players(match["id"]):
            poller.unwatch(p["user_id"])
        hub.send_match(match["code"], "match.ended", {"reason": "cancelled", "result_status": "none"})
    events.state(match["id"])
    log.info("admin cancelled match %s (was %s)", match["code"], match["status"])


def ban(admin_user, user_id: int) -> None:
    target = users.by_id(user_id)
    if target is None:
        raise AdminError("No such user.")
    if target["id"] == admin_user["id"]:
        raise AdminError("You can't ban yourself.")
    if target["banned_at"]:
        return
    # A new session version signs them out everywhere, even after an unban.
    db.execute("UPDATE users SET banned_at = ?, session_version = session_version + 1 WHERE id = ?",
               (clock.now(), user_id))
    _clear_waiting(target)
    _close_sockets(user_id)
    log.info("admin %s banned %s", admin_user["handle"], target["handle"])


def unban(admin_user, user_id: int) -> None:
    if users.by_id(user_id) is None:
        raise AdminError("No such user.")
    db.execute("UPDATE users SET banned_at = NULL WHERE id = ?", (user_id,))
    log.info("admin %s unbanned user %s", admin_user["handle"], user_id)


def _clear_waiting(user) -> None:
    """Take a banned user out of the ranked queue and any lobby; running matches are left alone."""
    ranked_queue.leave(user["id"])
    for r in db.all("SELECT m.code FROM match_players p JOIN matches m ON m.id = p.match_id"
                    " WHERE p.user_id = ? AND m.status = 'lobby'", (user["id"],)):
        try:
            service.leave(user, r["code"])
        except service.MatchError:
            pass


async def _close(ws) -> None:
    try:
        await ws.close(code=1008)
    except Exception:
        pass  # already gone


def _close_sockets(user_id: int) -> None:
    """Open tabs reconnect as a logged-out visitor."""
    for conn in list(hub.by_user.get(user_id, ())):
        try:
            asyncio.get_running_loop().create_task(_close(conn.ws))
        except RuntimeError:
            pass  # no loop (sync tests)
