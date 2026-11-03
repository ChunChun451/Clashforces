"""One WebSocket per browser tab. Anonymous sockets are allowed (spectators)."""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from urllib.parse import urlsplit

from app import clock, config
from app.auth import session, users
from app.cf.client import get_client
from app.matches import service, store, view
from app.poller.poller import poller
from app.ws.hub import Conn, hub

router = APIRouter()
log = logging.getLogger("ws")


def origin_allowed(origin: str | None, host: str | None) -> bool:
    """ALLOWED_ORIGINS set: Origin must be one of them. Unset: Origin's host must equal the Host header.
    Non-browser clients send no Origin; they can't carry a victim's cookies, so they're allowed."""
    if not origin:
        return True
    origin = origin.rstrip("/")
    if config.ALLOWED_ORIGINS:
        return origin in config.ALLOWED_ORIGINS
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        log.warning("rejected websocket from origin %s", ws.headers.get("origin"))
        await ws.close(code=1008)
        return
    await ws.accept()
    user = session.user_from_session(ws.scope.get("session") or {})
    uid = user["id"] if user else None
    conn = Conn(ws, uid)
    hub.add(conn)
    try:
        await ws.send_json({"type": "hello", "data": {
            "user": users.public(user) if user else None,
            "server_time": clock.now(),
            "cf_api": "down" if get_client().down else "ok",
        }})
        while True:
            msg = await ws.receive_json()
            if not isinstance(msg, dict):
                continue
            await handle(conn, user, msg.get("type"), msg.get("data") or {})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("websocket error")
    finally:
        hub.remove(conn)


async def handle(conn: Conn, user, type_: str, data: dict) -> None:
    code = str(data.get("code", "")).upper()[:12]
    if type_ == "ping":
        await conn.send({"type": "pong", "data": {"server_time": clock.now()}})
    elif type_ == "subscribe":
        match = store.match_by_code(code)
        if match is None:
            await conn.send({"type": "error", "data": {"message": "No match with that code."}})
            return
        hub.subscribe(conn, match["code"])
        await conn.send({"type": "match.state", "data": view.build(match, conn.user_id)})
    elif type_ == "unsubscribe":
        hub.unsubscribe(conn, code)
    elif type_ == "submitted" and user:
        try:
            queued = service.hint(user, code)
            await conn.send({"type": "hint.ack", "data": {"queued": queued}})
        except service.MatchError as e:
            await conn.send({"type": "error", "data": {"message": str(e)}})
    elif type_ == "focus" and user:
        try:
            poller.soft_hint(user["id"], float(data.get("away_s", 0)))
        except (TypeError, ValueError):
            pass
