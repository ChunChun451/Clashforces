"""The Players page: everyone on the site, what's happening, and challenges.

A challenge is an invite to a lobby that is saved, so it still reaches someone
who was offline when it was sent. It stays open while the lobby does.
"""

from app import clock, db
from app.auth import users
from app.matches import reveal, service, store
from app.ws.hub import hub

FEED_LIMIT = 30
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


# ---------- activity ----------

def _sides(m) -> list[dict]:
    """Finished match -> sides in place order. A side is a team, or one player."""
    rows = db.all("SELECT handle, cf_rating, place, team FROM match_players WHERE match_id = ? ORDER BY place, handle",
                  (m["id"],))
    if store.is_team(m):
        names = store.team_names(m)
        sides: dict[int, dict] = {}
        for r in rows:
            if r["team"] is None:
                continue
            s = sides.setdefault(r["team"], {"name": names[r["team"]] if r["team"] < len(names) else None,
                                             "place": r["place"], "players": []})
            s["players"].append({"handle": r["handle"], "cf_rating": r["cf_rating"]})
        return sorted(sides.values(), key=lambda s: (s["place"] or 99))
    return [{"name": None, "place": r["place"], "players": [{"handle": r["handle"], "cf_rating": r["cf_rating"]}]}
            for r in rows]


def _live_item(m) -> dict:
    s = store.settings(m)
    rows = db.all("SELECT handle, cf_rating FROM match_players WHERE match_id = ? ORDER BY joined_at", (m["id"],))
    return {"code": m["code"], "mode": m["mode"], "rules": m["rules"], "title": s.get("title"),
            "start_time": m["start_time"], "end_time": m["end_time"],
            "players": [{"handle": r["handle"], "cf_rating": r["cf_rating"]} for r in rows][:8], "n_players": len(rows)}


def activity() -> dict:
    """Live matches, plus a feed of finished matches and new members. Unlisted contests stay out."""
    listed = "(m.mode != 'contest' OR m.visibility = 'public')"
    live = [_live_item(m) for m in db.all(
        f"SELECT m.* FROM matches m WHERE m.status = 'running' AND {listed} ORDER BY m.start_time DESC LIMIT 12")]
    items = []
    for m in db.all(f"SELECT m.* FROM matches m WHERE m.status = 'ended' AND m.result_status = 'final' AND {listed}"
                    f" AND {reveal.UNLOCKED_SQL} ORDER BY m.ended_at DESC LIMIT ?", (FEED_LIMIT,)):
        sides = _sides(m)
        if len(sides) < 2 and m["mode"] != "contest":
            continue
        items.append({"kind": "match", "at": m["ended_at"], "code": m["code"], "mode": m["mode"], "rules": m["rules"],
                      "title": store.settings(m).get("title"), "end_reason": m["end_reason"], "sides": sides[:6],
                      "n_sides": len(sides),
                      "draw": len(sides) > 1 and sides[0]["place"] == sides[1]["place"]})
    for u in db.all("SELECT handle, cf_rating, created_at FROM users WHERE handle IS NOT NULL AND banned_at IS NULL"
                    " ORDER BY created_at DESC LIMIT ?", (FEED_LIMIT,)):
        items.append({"kind": "joined", "at": u["created_at"], "handle": u["handle"], "cf_rating": u["cf_rating"]})
    items.sort(key=lambda i: -(i["at"] or 0))
    return {"live": live, "items": items[:FEED_LIMIT], "server_time": clock.now()}


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

