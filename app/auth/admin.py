"""Who is an admin: a logged-in user whose handle is listed in ADMIN_HANDLES."""

from fastapi import HTTPException, Request

from app import config
from app.auth import session


def is_admin(user) -> bool:
    return bool(user and user["handle"] and user["handle"].lower() in config.ADMIN_HANDLES)


async def require_admin(request: Request):
    """FastAPI dependency. Everyone else gets the same 404 as a page that doesn't exist."""
    user = session.user_from_session(request.session)
    if not is_admin(user):
        raise HTTPException(404, "Not Found")
    return user
