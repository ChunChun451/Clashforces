"""Captains' draft: the host names one captain per team, then captains take turns
picking players in snake order (A B C C B A ...). Each pick has a time limit;
when it runs out the highest-rated player left is picked automatically.

State lives in the match settings under "draft", so a restart doesn't lose it:
{"active": bool, "done": bool, "pos": turn number, "deadline": unix time}.
"""

from app import clock, config, db
from app.core import team as team_core
from app.matches import events, store
from app.matches.service import MatchError, _hosted_lobby, _team_conf
from app.ws.hub import hub


def save_settings(match_id: int, s: dict) -> None:
    import json
    db.execute("UPDATE matches SET settings = ? WHERE id = ?", (json.dumps(s), match_id))


def state(match) -> dict | None:
    s = store.settings(match)
    if s.get("fill") != "draft":
        return None
    d = s.get("draft") or {}
    out = {"active": bool(d.get("active")), "done": bool(d.get("done")), "deadline": d.get("deadline"),
           "pick_seconds": config.DRAFT_PICK_S, "team": None}
    if d.get("active"):
        out["team"] = current_team(match, d)
    return out


def _counts(match) -> list[int]:
    count, _, _ = _team_conf(match)
    players = store.players(match["id"])
    return [sum(1 for p in players if p["team"] == t) for t in range(count)]


def current_team(match, d: dict) -> int:
    count, _, _ = _team_conf(match)
    return team_core.snake_order(count, d["pos"] + 1)[d["pos"]]


def pool(match) -> list:
    """Players nobody has picked yet, strongest first (ties: who joined first)."""
    players = [p for p in store.players(match["id"]) if p["team"] is None]
    return sorted(players, key=lambda p: (-(p["current_rating"] or 0), p["joined_at"], p["user_id"]))


def set_captain(user, code: str, team: int, player_id: int | None) -> None:
    match = _hosted_lobby(user, code)
    count, size, s = _team_conf(match)
    if s.get("fill") != "draft":
        raise MatchError("This match doesn't use a captains' draft.")
    if (s.get("draft") or {}).get("active"):
        raise MatchError("The draft has already started.")
    if not 0 <= team < count:
        raise MatchError("That team doesn't exist.")
    store.ensure_teams(match["id"], count)
    old = db.scalar("SELECT captain_id FROM match_teams WHERE match_id = ? AND idx = ?", (match["id"], team))
    with db.transaction():
        if old is not None:
            db.execute("UPDATE match_players SET team = NULL WHERE match_id = ? AND user_id = ?", (match["id"], old))
        if player_id is not None:
            p = next((p for p in store.players(match["id"]) if p["user_id"] == player_id), None)
            if p is None:
                raise MatchError("That player is not in this lobby.")
            db.execute("UPDATE match_teams SET captain_id = NULL WHERE match_id = ? AND captain_id = ?",
                       (match["id"], player_id))
            db.execute("UPDATE match_players SET team = NULL WHERE match_id = ? AND team = ?", (match["id"], team))
            db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?",
                       (team, match["id"], player_id))
        db.execute("UPDATE match_teams SET captain_id = ? WHERE match_id = ? AND idx = ?",
                   (player_id, match["id"], team))
        s["draft"] = None
        save_settings(match["id"], s)
    events.state(match["id"])


def start(user, code: str) -> None:
    match = _hosted_lobby(user, code)
    count, size, s = _team_conf(match)
    if s.get("fill") != "draft":
        raise MatchError("This match doesn't use a captains' draft.")
    if (s.get("draft") or {}).get("active"):
        return
    captains = {t["idx"]: t["captain_id"] for t in store.teams(match["id"])}
    missing = [store.team_names(match)[i] for i in range(count) if not captains.get(i)]
    if missing:
        raise MatchError(f"Pick a captain for {', '.join(missing)} first.")
    # Only captains stay placed; everyone else goes back into the pool.
    db.execute("UPDATE match_players SET team = NULL, ready = 0 WHERE match_id = ? AND user_id NOT IN"
               " (SELECT captain_id FROM match_teams WHERE match_id = ? AND captain_id IS NOT NULL)",
               (match["id"], match["id"]))
    s["draft"] = {"active": True, "done": False, "pos": -1, "deadline": None}
    save_settings(match["id"], s)
    _advance(store.match_by_id(match["id"]))


def pick(user, code: str, player_id: int) -> None:
    match = store.match_by_code(code)
    if match is None:
        raise MatchError("No match with that code.")
    d = (store.settings(match).get("draft") or {})
    if match["status"] != "lobby" or not d.get("active"):
        raise MatchError("The draft isn't running.")
    t = current_team(match, d)
    captain = db.scalar("SELECT captain_id FROM match_teams WHERE match_id = ? AND idx = ?", (match["id"], t))
    if user["id"] != captain:
        raise MatchError(f"It's {store.team_names(match)[t]}'s turn to pick.")
    if not any(p["user_id"] == player_id for p in pool(match)):
        raise MatchError("That player has already been picked.")
    _place(match, t, player_id, auto=False)


def auto_pick(match) -> None:
    """The pick timer ran out: take the highest-rated player left."""
    d = store.settings(match).get("draft") or {}
    if not d.get("active"):
        return
    left = pool(match)
    if not left:
        _advance(match)
        return
    _place(match, current_team(match, d), left[0]["user_id"], auto=True)


def _place(match, team: int, player_id: int, auto: bool) -> None:
    db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?", (team, match["id"], player_id))
    handle = db.scalar("SELECT handle FROM users WHERE id = ?", (player_id,))
    hub.send_match(match["code"], "draft.pick", {"team": team, "name": store.team_names(match)[team],
                                                 "user_id": player_id, "handle": handle, "auto": auto})
    _advance(store.match_by_id(match["id"]))


def _advance(match) -> None:
    """Move to the next team that still has room, or finish the draft."""
    count, size, s = _team_conf(match)
    d = s.get("draft") or {}
    counts = _counts(match)
    left = pool(match)
    pos = d.get("pos", -1)
    if left and sum(1 for c in counts if c < size) > 1:  # with one team left there's nothing to choose
        for _ in range(2 * count):
            pos += 1
            t = team_core.snake_order(count, pos + 1)[pos]
            if counts[t] < size:
                s["draft"] = {"active": True, "done": False, "pos": pos,
                              "deadline": clock.now() + config.DRAFT_PICK_S}
                save_settings(match["id"], s)
                events.state(match["id"])
                return
    # Done. Anyone left over goes to the smallest team that has room; if every team is full they wait
    # unplaced (the host can move them, or they sit out).
    for p in left:
        t = team_core.smallest_team(counts, size)
        if t is None:
            break
        counts[t] += 1
        db.execute("UPDATE match_players SET team = ? WHERE match_id = ? AND user_id = ?",
                   (t, match["id"], p["user_id"]))
    s["draft"] = {"active": False, "done": True, "pos": pos, "deadline": None}
    save_settings(match["id"], s)
    hub.send_match(match["code"], "draft.done", {})
    events.state(match["id"])


def tick(now: int) -> None:
    """Called every second by the lifecycle ticker: auto-pick when a captain runs out of time."""
    for m in db.all("SELECT * FROM matches WHERE status = 'lobby' AND settings LIKE '%\"active\": true%'"):
        d = store.settings(m).get("draft") or {}
        if d.get("active") and d.get("deadline") is not None and now >= d["deadline"]:
            auto_pick(m)
