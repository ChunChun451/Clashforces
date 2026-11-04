from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import clock, community, db
from app.auth import session, users
from app.matches import stats_data
from app.ws.hub import hub

router = APIRouter(prefix="/api")


class FriendIn(BaseModel):
    handle: str


class InviteIn(BaseModel):
    handle: str


def _user_or_404(handle: str):
    u = users.by_handle(handle)
    if u is None:
        raise HTTPException(404, "No player with that handle here.")
    return u


@router.get("/users/{handle}")
async def profile(handle: str, me=Depends(session.current_user)):
    u = _user_or_404(handle)
    out = users.public(u)
    out["online"] = hub.online(u["id"])
    out["is_friend"] = bool(me and db.one("SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?", (me["id"], u["id"])))
    out["is_me"] = bool(me and me["id"] == u["id"])
    return out


@router.get("/users/{handle}/stats")
async def user_stats(handle: str):
    return stats_data.user_stats(_user_or_404(handle)["id"])


@router.get("/users/{handle}/rating-history")
async def rating_history(handle: str):
    return {"points": stats_data.rating_history(_user_or_404(handle)["id"])}


@router.get("/users/{handle}/matches")
async def user_matches(handle: str):
    return {"matches": stats_data.recent_matches(_user_or_404(handle)["id"])}


@router.get("/h2h/{handle}")
async def h2h(handle: str, me=Depends(session.require_user)):
    other = _user_or_404(handle)
    return {"handle": other["handle"], **stats_data.h2h(me["id"], other["id"])}


# ---------- friends ----------

def friend_rows(user_id: int):
    return db.all("SELECT u.* FROM friends f JOIN users u ON u.id = f.friend_id WHERE f.user_id = ? ORDER BY u.duel_rating DESC",
                  (user_id,))


@router.get("/friends")
async def friends(me=Depends(session.require_user)):
    out = []
    for u in friend_rows(me["id"]):
        h = stats_data.h2h(me["id"], u["id"])
        out.append({**users.public(u), "online": hub.online(u["id"]),
                    "h2h": {k: h[k] for k in ("win", "loss", "draw")}})
    return {"friends": out}


@router.post("/friends")
async def add_friend(body: FriendIn, me=Depends(session.require_user)):
    other = users.by_handle(body.handle.strip())
    if other is None:
        raise HTTPException(404, "That handle hasn't joined Clashforces yet.")
    if other["id"] == me["id"]:
        raise HTTPException(400, "That's you.")
    db.execute("INSERT OR IGNORE INTO friends(user_id, friend_id, created_at) VALUES (?,?,?)",
               (me["id"], other["id"], clock.now()))
    return {"ok": True}


@router.delete("/friends/{handle}")
async def remove_friend(handle: str, me=Depends(session.require_user)):
    other = _user_or_404(handle)
    db.execute("DELETE FROM friends WHERE user_id = ? AND friend_id = ?", (me["id"], other["id"]))
    return {"ok": True}


@router.get("/friends/leaderboard")
async def leaderboard(me=Depends(session.require_user)):
    rows = list(friend_rows(me["id"])) + [me]
    rows.sort(key=lambda u: -u["duel_rating"])
    return {"rows": [{**users.public(u), "is_me": u["id"] == me["id"], "online": hub.online(u["id"])} for u in rows]}


@router.post("/matches/{code}/invite")
async def invite(code: str, body: InviteIn, me=Depends(session.require_user)):
    """Saved as a challenge, so it also reaches someone who's offline right now."""
    try:
        return community.send(me, code, body.handle)
    except community.ChallengeError as e:
        raise HTTPException(400, str(e))


def presence_changed(user_id: int, is_online: bool) -> None:
    """Tell everyone who follows this user."""
    u = users.by_id(user_id)
    if u is None:
        return
    for r in db.all("SELECT user_id FROM friends WHERE friend_id = ?", (user_id,)):
        hub.send_user(r["user_id"], "presence", {"handle": u["handle"], "online": is_online})


if presence_changed not in hub.presence_listeners:
    hub.presence_listeners.append(presence_changed)
