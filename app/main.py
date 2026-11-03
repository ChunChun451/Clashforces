"""FastAPI app: wires routes and starts background tasks."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import config, db
from app.cf import cache
from app.cf.client import get_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.connect()
    tasks = []
    if config.BACKGROUND_TASKS:
        cf = get_client()
        tasks.append(asyncio.create_task(cf.run(), name="cf-worker"))
        tasks.append(asyncio.create_task(cache.refresh_loop(cf), name="cache-refresh"))
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Clashforces", lifespan=lifespan)


@app.get("/api/health")
def health():
    cf = get_client()
    return {"cf_api": "down" if cf.down else "ok", "lanes": cf.queue_depths()}
