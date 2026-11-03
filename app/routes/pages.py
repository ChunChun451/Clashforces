"""Serves the static HTML pages. All data is fetched by JS from /api."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC = Path(__file__).resolve().parents[2] / "static"
router = APIRouter()


def _page(name: str):
    async def handler():
        return FileResponse(STATIC / f"{name}.html")
    return handler


PAGES = {
    "/": "index",
    "/login": "login",
    "/settings": "settings",
    "/new": "new",
    "/events": "events",
    "/events/new": "event-new",
    "/m/{code}": "match",
    "/m/{code}/screen": "screen",
    "/queue": "queue",
    "/upsolve": "upsolve",
    "/friends": "friends",
    "/u/{handle}": "profile",
}

for path, name in PAGES.items():
    router.add_api_route(path, _page(name), methods=["GET", "HEAD"], include_in_schema=False)


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")
