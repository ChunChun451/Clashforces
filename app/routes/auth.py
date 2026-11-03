import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app import config, db
from app.auth import session, users, verify
from app.cf.client import CFError
from app.matches import locks
from app.poller import upsolve

router = APIRouter(prefix="/api")

_starts: dict[str, deque] = defaultdict(deque)  # ip -> recent start times
_SWEEP_EVERY_S = 600
_last_sweep = 0.0


def _rate_limit(ip: str, limit: int = 10, window: int = 600) -> None:
    global _last_sweep
    now = time.time()
    if now - _last_sweep > _SWEEP_EVERY_S:
        # Forget IPs whose attempts have all expired, so the dict doesn't grow forever.
        _last_sweep = now
        for key in [k for k, q in _starts.items() if not q or now - q[-1] > window]:
            del _starts[key]
    q = _starts[ip]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "Too many attempts. Wait a few minutes.")
    q.append(now)


class StartIn(BaseModel):
    handle: str


class CheckIn(BaseModel):
    task_id: str


class SettingsIn(BaseModel):
    discord_webhook: str | None = None


@router.post("/auth/start")
async def auth_start(body: StartIn, request: Request):
    _rate_limit(request.client.host if request.client else "?")
    try:
        return await verify.start(body.handle)
    except verify.VerifyError as e:
        raise HTTPException(400, str(e))


@router.post("/auth/check")
async def auth_check(body: CheckIn, request: Request):
    try:
        view, user_id = await verify.check(body.task_id)
    except verify.VerifyError as e:
        raise HTTPException(400, str(e))
    task = db.one("SELECT user_id FROM verifications WHERE id = ?", (body.task_id,))
    if user_id is not None and task["user_id"] is None:
        session.login(request, user_id)  # sign-up / login; a handle change keeps the session
    return view


@router.post("/auth/logout")
async def auth_logout(request: Request, everywhere: bool = False):
    session.logout(request, everywhere)
    return {"ok": True}


@router.get("/me")
async def me(user=Depends(session.current_user)):
    if user is None:
        return {"user": None}
    users.touch_seen(user["id"])
    lock = locks.current(user["id"])
    return {
        "user": users.public(user),
        "discord_webhook": user["discord_webhook"],
        "active": None if lock is None else {"kind": lock["kind"], "code": lock["code"]},
    }


@router.post("/me/handle/start")
async def handle_change_start(body: StartIn, user=Depends(session.require_user)):
    try:
        return await verify.start(body.handle, user_id=user["id"])
    except verify.VerifyError as e:
        raise HTTPException(400, str(e))


@router.patch("/me/settings")
async def update_settings(body: SettingsIn, user=Depends(session.require_user)):
    hook = (body.discord_webhook or "").strip() or None
    if hook and not hook.startswith(config.DISCORD_PREFIX):
        raise HTTPException(400, "Discord webhook URLs start with " + config.DISCORD_PREFIX)
    db.execute("UPDATE users SET discord_webhook = ? WHERE id = ?", (hook, user["id"]))
    return {"ok": True}


@router.get("/me/upsolve")
async def my_upsolve(user=Depends(session.require_user)):
    return {"items": upsolve.items(user["id"]), "can_refresh": upsolve.can_refresh(user["id"])}


@router.post("/me/upsolve/refresh")
async def refresh_upsolve(user=Depends(session.require_user)):
    try:
        done = await upsolve.refresh(user["id"])
    except CFError:
        raise HTTPException(503, "Codeforces is not answering. Try again later.")
    if not done:
        raise HTTPException(429, "You refreshed recently. Try again in a few minutes.")
    return {"items": upsolve.items(user["id"]), "can_refresh": False}
