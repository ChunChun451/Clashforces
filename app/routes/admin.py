"""Owner-only admin page and API. Anyone who isn't an admin gets a plain 404."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app import admin
from app.auth.admin import require_admin
from app.routes.pages import STATIC

router = APIRouter()


def _run(fn, *args):
    try:
        return fn(*args)
    except admin.AdminError as e:
        raise HTTPException(400, str(e))


@router.api_route("/admin", methods=["GET", "HEAD"], include_in_schema=False)
async def admin_page(_=Depends(require_admin)):
    return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-store"})


@router.get("/api/admin/stats", include_in_schema=False)
async def stats(_=Depends(require_admin)):
    return admin.stats()


@router.get("/api/admin/users", include_in_schema=False)
async def users(q: str = "", sort: str = "joined", page: int = 1, _=Depends(require_admin)):
    return admin.list_users(q, sort, page)


@router.get("/api/admin/matches", include_in_schema=False)
async def matches(state: str = "", mode: str = "", page: int = 1, _=Depends(require_admin)):
    return admin.list_matches(state, mode, page)


@router.get("/api/admin/activity", include_in_schema=False)
async def activity(_=Depends(require_admin)):
    return admin.activity()


@router.post("/api/admin/matches/{code}/cancel", include_in_schema=False)
async def cancel(code: str, _=Depends(require_admin)):
    _run(admin.cancel_match, code.upper())
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/ban", include_in_schema=False)
async def ban(user_id: int, me=Depends(require_admin)):
    _run(admin.ban, me, user_id)
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/unban", include_in_schema=False)
async def unban(user_id: int, me=Depends(require_admin)):
    _run(admin.unban, me, user_id)
    return {"ok": True}
