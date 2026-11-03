"""Signed-cookie sessions. The cookie holds the user id and a session version.

Route handlers and dependencies are all `async def` so they run on the event
loop thread: SQLite access stays single-threaded and the hub can send messages.
"""

from fastapi import HTTPException, Request, WebSocket

from app import db
from app.auth import users


def login(request: Request, user_id: int) -> None:
    row = users.by_id(user_id)
    request.session.clear()
    request.session.update({"uid": user_id, "sv": row["session_version"]})


def logout(request: Request, everywhere: bool = False) -> None:
    uid = request.session.get("uid")
    if everywhere and uid:
        db.execute("UPDATE users SET session_version = session_version + 1 WHERE id = ?", (uid,))
    request.session.clear()


def user_from_session(session: dict):
    uid = session.get("uid")
    if not uid:
        return None
    row = users.by_id(uid)
    if row is None or row["session_version"] != session.get("sv"):
        return None
    return row


async def current_user(request: Request):
    """FastAPI dependency: the logged-in user row or None."""
    return user_from_session(request.session)


async def require_user(request: Request):
    """FastAPI dependency: the logged-in user row, or 401."""
    row = user_from_session(request.session)
    if row is None:
        raise HTTPException(401, "Please log in.")
    return row


