"""Serves the static HTML pages. All data is fetched by JS from /api."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

STATIC = Path(__file__).resolve().parents[2] / "static"
router = APIRouter()


def _page(name: str):
    def handler():
        return FileResponse(STATIC / f"{name}.html")
    return handler


PAGES = {
    "/": "index",
    "/login": "login",
    "/settings": "settings",
}

for path, name in PAGES.items():
    router.add_api_route(path, _page(name), include_in_schema=False)
