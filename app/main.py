"""FastAPI app: wires routes and starts background tasks."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import config, db
from app.auth import users
from app.auth.session import login, user_from_session
from app.cf import cache
from app.cf.client import get_client
from app.matches import lifecycle
from app.matches import queue as ranked_queue
from app.poller import upsolve
from app.poller.poller import poller
from app.routes import admin as admin_routes
from app.routes import auth as auth_routes
from app.routes import matches as match_routes
from app.routes import queue as queue_routes
from app.routes import community as community_routes
from app.routes import users as user_routes
from app.routes import ws as ws_routes
from app.ws.hub import hub
from app.routes import pages

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.check_production_settings()
    db.connect()
    tasks = []
    if config.BACKGROUND_TASKS:
        cf = get_client()
        cf.on_health_change.append(lambda down: hub.send_all("api.status", {"cf_api": "down" if down else "ok"}))
        tasks.append(asyncio.create_task(cf.run(), name="cf-worker"))
        tasks.append(asyncio.create_task(cache.refresh_loop(cf), name="cache-refresh"))
        tasks.append(asyncio.create_task(poller.run(), name="poller"))
        tasks.append(asyncio.create_task(lifecycle.run(), name="lifecycle"))
        tasks.append(asyncio.create_task(ranked_queue.run(), name="ranked-queue"))
        tasks.append(asyncio.create_task(upsolve.sweep_loop(), name="upsolve-sweep"))
    else:
        lifecycle.setup()
        ranked_queue.setup()
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Clashforces", lifespan=lifespan)


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Writes to the API must come from our own JS (which sets this header)."""
    if (request.url.path.startswith("/api/") and request.method in ("POST", "PATCH", "PUT", "DELETE")
            and request.headers.get("x-requested-with") != "fetch"):
        return JSONResponse({"detail": "Missing X-Requested-With header."}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """Browsers check static files and pages with the server before reusing them (a cheap 304),
    so a deploy never leaves anyone running old CSS or JS."""
    response = await call_next(request)
    if request.url.path.startswith("/static/") or response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@app.middleware("http")
async def dev_auto_login(request: Request, call_next):
    """Local development: sign localhost visitors in as config.DEV_AUTO_LOGIN."""
    if (config.DEV_AUTO_LOGIN and request.client and request.client.host in LOOPBACK
            and request.url.hostname in LOOPBACK and not user_from_session(request.session)):
        row = users.by_handle(config.DEV_AUTO_LOGIN)
        if row is not None:
            login(request, row["id"])
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    session_cookie="cf_session",
    max_age=config.SESSION_MAX_AGE_S,
    same_site="lax",
    https_only=config.COOKIE_SECURE,
)
app.include_router(auth_routes.router)
app.include_router(match_routes.router)
app.include_router(queue_routes.router)
app.include_router(user_routes.router)
app.include_router(community_routes.router)
app.include_router(ws_routes.router)
app.include_router(pages.router)
app.include_router(admin_routes.router)
app.mount("/static", StaticFiles(directory=pages.STATIC), name="static")


@app.get("/api/health")
async def health():
    cf = get_client()
    return {"cf_api": "down" if cf.down else "ok", "lanes": cf.queue_depths(),
            "active_players": len(poller.watches),
            # The VM's auto-deploy skips restarts while this is above 0.
            "running_matches": db.scalar("SELECT COUNT(*) FROM matches WHERE status IN ('starting', 'running')")}
