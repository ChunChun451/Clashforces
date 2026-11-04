"""The Players page: everyone on the site, and challenges.

A challenge is an invite to a lobby that is saved, so it still reaches someone
who was offline when it was sent. It stays open while the lobby does.
"""

from app import clock, db
from app.auth import users
from app.matches import service, store
from app.ws.hub import hub

CHALLENGES_PER_HOUR = 15


class ChallengeError(Exception):
    pass


# ---------- members ----------

def players(viewer_id: int | None) -> list[dict]:
    rows = db.all(
        "SELECT u.*, (SELECT COUNT(*) FROM match_players mp JOIN matches m ON m.id = mp.match_id"
        "   WHERE mp.user_id = u.id AND m.result_status = 'final') AS played,"
        " (SELECT 1 FROM friends f WHERE f.user_id = ? AND f.friend_id = u.id) AS is_friend"
        " FROM users u WHERE u.handle IS NOT NULL AND u.banned_at IS NULL", (viewer_id or -1,))
    out = []
    for u in rows:
        item = {**users.public(u), "online": hub.online(u["id"]) or u["id"] == viewer_id, "played": u["played"],
                "joined_at": u["created_at"], "last_seen_at": u["last_seen_at"],
                "is_me": u["id"] == viewer_id, "is_friend": bool(u["is_friend"]), "playing": None}
        live = store.running_match_for(u["id"])
        if live is not None and (live["mode"] != "contest" or live["visibility"] == "public"):
            item["playing"] = {"code": live["code"], "mode": live["mode"]}  # anyone can watch a running match
        out.append(item)
    out.sort(key=lambda p: (not p["is_me"], not (p["online"] or p["playing"]), -(p["last_seen_at"] or p["joined_at"] or 0)))
    return out


# ---------- challenges ----------

def _open_lobby_sql(alias: str = "m") -> str:
    return f"{alias}.status = 'lobby'"


def send(me, code: str, handle: str) -> dict:
    """Challenge `handle` to the lobby `code`, which `me` is in. Saved, and pushed live if they're online."""
    match = store.match_by_code(code)
    if match is None or match["status"] != "lobby":
        raise ChallengeError("You can only invite to a match that hasn't started.")
    in_match = {p["user_id"] for p in store.players(match["id"])}
    if me["id"] not in in_match:
        raise ChallengeError("Only players can invite.")
    other = users.by_handle(handle.strip())
    if other is None or other["banned_at"]:
        raise ChallengeError("No player with that handle here.")
    if other["id"] == me["id"]:
        raise ChallengeError("That's you.")
    if other["id"] in in_match:
        raise ChallengeError(f"{other['handle']} is already in this match.")
    now = clock.now()
    recent = db.scalar("SELECT COUNT(*) FROM challenges WHERE from_id = ? AND created_at > ?", (me["id"], now - 3600))
    if recent >= CHALLENGES_PER_HOUR:
        raise ChallengeError("That's a lot of challenges for one hour. Try again a bit later.")
    db.execute(
        "INSERT INTO challenges(match_id, from_id, to_id, created_at) VALUES (?,?,?,?)"
        " ON CONFLICT(match_id, to_id) DO UPDATE SET from_id = excluded.from_id, created_at = excluded.created_at,"
        " declined_at = NULL", (match["id"], me["id"], other["id"], now))
    hub.send_user(other["id"], "invite", {"from": me["handle"], "code": match["code"], "mode": match["mode"]})
    hub.send_user(other["id"], "challenges", {"count": incoming_count(other["id"])})
    return {"ok": True, "online": hub.online(other["id"]), "handle": other["handle"]}


def _summary(m) -> dict:
    s = store.settings(m)
    out = {"rules": m["rules"], "duration_min": s.get("duration_min"), "count": s.get("count"),
           "custom": bool(s.get("custom_problems"))}
    if not out["custom"]:
        out.update(rating_min=s.get("rating_min"), rating_max=s.get("rating_max"))
    if store.is_team(m):
        out.update(team_size=store.team_size(m), team_count=store.team_count(m))
    return out


def _incoming_rows(user_id: int):
    return [r for r in db.all(
        "SELECT c.id, c.created_at, m.*, u.handle AS from_handle, u.cf_rating AS from_rating,"
        " (SELECT COUNT(*) FROM match_players mp WHERE mp.match_id = m.id) AS n_players"
        " FROM challenges c JOIN matches m ON m.id = c.match_id JOIN users u ON u.id = c.from_id"
        f" WHERE c.to_id = ? AND c.declined_at IS NULL AND {_open_lobby_sql()}"
        " AND NOT EXISTS (SELECT 1 FROM match_players mp WHERE mp.match_id = m.id AND mp.user_id = c.to_id)"
        " ORDER BY c.created_at DESC", (user_id,)) if r["n_players"] < service.max_players(r)]


def incoming_count(user_id: int) -> int:
    return len(_incoming_rows(user_id))


def lists(user_id: int) -> dict:
    incoming = [{"id": r["id"], "at": r["created_at"], "code": r["code"], "mode": r["mode"],
                 "from": {"handle": r["from_handle"], "cf_rating": r["from_rating"]},
                 "settings": _summary(r)} for r in _incoming_rows(user_id)]
    outgoing = [{"id": r["id"], "at": r["created_at"], "code": r["code"], "mode": r["mode"],
                 "to": {"handle": r["to_handle"], "cf_rating": r["to_rating"]},
                 "status": "joined" if r["joined"] else "declined" if r["declined_at"] else "waiting"}
                for r in db.all(
        "SELECT c.*, m.code, m.mode, u.handle AS to_handle, u.cf_rating AS to_rating,"
        " EXISTS (SELECT 1 FROM match_players mp WHERE mp.match_id = m.id AND mp.user_id = c.to_id) AS joined"
        " FROM challenges c JOIN matches m ON m.id = c.match_id JOIN users u ON u.id = c.to_id"
        f" WHERE c.from_id = ? AND {_open_lobby_sql()} ORDER BY c.created_at DESC", (user_id,))]
    return {"incoming": incoming, "outgoing": outgoing}


def decline(me, challenge_id: int) -> None:
    c = db.one("SELECT c.*, m.code FROM challenges c JOIN matches m ON m.id = c.match_id WHERE c.id = ? AND c.to_id = ?",
               (challenge_id, me["id"]))
    if c is None:
        raise ChallengeError("That challenge is gone.")
    db.execute("UPDATE challenges SET declined_at = ? WHERE id = ?", (clock.now(), challenge_id))
    hub.send_user(c["from_id"], "challenge_declined", {"handle": me["handle"], "code": c["code"]})
    hub.send_user(me["id"], "challenges", {"count": incoming_count(me["id"])})

