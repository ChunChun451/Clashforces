"""The ONLY way this app talks to the Codeforces API.

One worker sends one request at a time, at least CF_MIN_INTERVAL_S after the
previous request finished. Callers pick a lane; lower lane number = higher
priority. The POLL lane is guaranteed at least 1 of every CF_POLL_MIN_SHARE
slots while it has work, so live polling never starves.
"""

import asyncio
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Callable

import httpx

from app import config

log = logging.getLogger("cf")


class Lane(IntEnum):
    URGENT = 1       # final checks, clinch confirmation, user.info at duel start
    INTERACTIVE = 2  # verification, "I submitted", TESTING re-checks, lobby history top-ups
    POLL = 3         # priority scheduler for running matches
    BACKGROUND = 4   # cache refresh, upsolve sweeps


class CFError(Exception):
    pass


class HandleNotFound(CFError):
    def __init__(self, handle: str, comment: str = ""):
        super().__init__(comment or f"handle not found: {handle}")
        self.handle = handle


class CFUnavailable(CFError):
    """Gave up after all retries."""


_NOT_FOUND = re.compile(r"handle (\S+) not found", re.IGNORECASE)


@dataclass
class _Job:
    method: str
    params: dict | None
    prepare: Callable[[], dict | None] | None
    timeout: float
    future: asyncio.Future
    attempts: int = 0
    ready_at: float = 0.0


@dataclass
class _Retryable(Exception):
    reason: str


class CFClient:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self._http = http or httpx.AsyncClient(headers={"User-Agent": "clashforces"})
        self._clock = clock
        self._sleep = sleep
        self._lanes: dict[Lane, deque[_Job]] = {lane: deque() for lane in Lane}
        self._next_allowed = 0.0
        self._non_poll_streak = 0
        self._wake = asyncio.Event()
        self.failures_in_a_row = 0
        self.down = False
        self.on_health_change: list[Callable[[bool], Any]] = []
        self.requests_sent = 0

    # ---------- public ----------

    async def call(
        self,
        method: str,
        params: dict | None = None,
        lane: Lane = Lane.INTERACTIVE,
        timeout: float | None = None,
        prepare: Callable[[], dict | None] | None = None,
    ):
        """Queue a call and wait for its `result`.

        `prepare`, if given, is called right before sending and returns the
        params (or None to skip; the call then returns None without using a slot).
        """
        future = asyncio.get_running_loop().create_future()
        job = _Job(method, params, prepare, timeout or config.CF_TIMEOUT_S, future)
        self._lanes[lane].append(job)
        self._wake.set()
        return await future

    def queue_depths(self) -> dict[str, int]:
        return {lane.name: len(q) for lane, q in self._lanes.items()}

    async def run(self) -> None:
        while True:
            try:
                did_work = await self.process_one()
            except asyncio.CancelledError:
                raise
            except Exception:  # never let the worker die
                log.exception("cf worker error")
                did_work = False
            if not did_work:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------- worker ----------

    async def process_one(self) -> bool:
        """Send at most one request. Returns False when nothing was ready."""
        if not any(self._lanes.values()):
            return False
        wait = self._next_allowed - self._clock()
        if wait > 0:
            await self._sleep(wait)
        job, lane = self._pick()
        if job is None:
            soonest = min(j.ready_at for q in self._lanes.values() for j in q)
            await self._sleep(max(0.0, soonest - self._clock()))
            return True
        self._lanes[lane].remove(job)
        if job.future.done():  # caller went away
            return True
        params = job.params
        if job.prepare is not None:
            params = job.prepare()
            if params is None:
                job.future.set_result(None)
                return True
            job.params, job.prepare = params, None
        await self._send(job, lane)
        return True

    def _pick(self) -> tuple[_Job | None, Lane | None]:
        now = self._clock()
        ready = {lane: next((j for j in q if j.ready_at <= now), None) for lane, q in self._lanes.items()}
        poll_ready = ready[Lane.POLL] is not None
        if poll_ready and self._non_poll_streak >= config.CF_POLL_MIN_SHARE - 1:
            self._non_poll_streak = 0
            return ready[Lane.POLL], Lane.POLL
        for lane in Lane:
            if ready[lane] is not None:
                if lane == Lane.POLL:
                    self._non_poll_streak = 0
                elif poll_ready:
                    self._non_poll_streak += 1
                return ready[lane], lane
        return None, None

    async def _send(self, job: _Job, lane: Lane) -> None:
        job.attempts += 1
        try:
            result = await self._request(job)
        except HandleNotFound as e:
            self._mark_success()  # the API answered; it's healthy
            job.future.set_exception(e)
        except _Retryable as e:
            self._mark_failure()
            log.warning("cf %s failed (%s), attempt %d", job.method, e.reason, job.attempts)
            if job.attempts >= config.CF_MAX_ATTEMPTS:
                job.future.set_exception(CFUnavailable(f"{job.method}: {e.reason}"))
            else:
                delays = config.CF_BACKOFF_S
                delay = delays[job.attempts - 1] if job.attempts <= len(delays) else config.CF_BACKOFF_CAP_S
                job.ready_at = self._clock() + min(delay, config.CF_BACKOFF_CAP_S)
                self._lanes[lane].appendleft(job)
        except Exception as e:
            # A bug while handling the response must not leave the caller waiting forever.
            log.exception("cf %s: unexpected error", job.method)
            if not job.future.done():
                job.future.set_exception(CFError(f"{job.method}: unexpected error ({type(e).__name__})"))
        else:
            self._mark_success()
            if not job.future.done():
                job.future.set_result(result)
        finally:
            self._next_allowed = self._clock() + config.CF_MIN_INTERVAL_S

    async def _request(self, job: _Job):
        self.requests_sent += 1
        url = f"{config.CF_API_BASE}/{job.method}"
        try:
            resp = await self._http.get(url, params=job.params, timeout=job.timeout)
        except httpx.HTTPError as e:
            raise _Retryable(type(e).__name__)
        try:
            data = resp.json()
        except ValueError:
            raise _Retryable(f"bad json (http {resp.status_code})")
        if not isinstance(data, dict) or "status" not in data:
            raise _Retryable(f"unexpected body (http {resp.status_code})")
        if data["status"] == "OK":
            return data.get("result")
        comment = str(data.get("comment", ""))
        match = _NOT_FOUND.search(comment)
        if match:
            raise HandleNotFound(match.group(1), comment)
        raise _Retryable(f"FAILED: {comment[:120]}")

    def _mark_success(self) -> None:
        self.failures_in_a_row = 0
        if self.down:
            self._set_down(False)

    def _mark_failure(self) -> None:
        self.failures_in_a_row += 1
        if not self.down and self.failures_in_a_row >= config.CF_DOWN_AFTER_FAILURES:
            self._set_down(True)

    def _set_down(self, down: bool) -> None:
        self.down = down
        for callback in self.on_health_change:
            try:
                callback(down)
            except Exception:
                log.exception("health callback failed")


# The app uses one shared client. main.py sets it at startup; tests set a fake.
_client: CFClient | None = None


def get_client() -> CFClient:
    global _client
    if _client is None:
        _client = CFClient()
    return _client


def set_client(client: CFClient | None) -> None:
    global _client
    _client = client
