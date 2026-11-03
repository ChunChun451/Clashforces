"""The submission poller.

- POLL lane: one request in flight at a time; the player is chosen at the
  moment the slot opens (core/schedule.pick), so the choice is always fresh.
- INTERACTIVE lane: "I submitted" hints (after HARD_HINT_DELAY_S) and
  re-checks of TESTING submissions every TESTING_RECHECK_S.
- URGENT lane: final checks, used by the match lifecycle.

Every change is passed to listeners as (user_id, changed_submissions).
Scoring never uses the time we saw a submission, only creationTimeSeconds.
"""

import asyncio
import logging
from typing import Callable

from app import clock, config, db
from app.cf.client import CFError, Lane, get_client
from app.cf.parse import parse_submissions
from app.core import schedule
from app.core.schedule import Watch
from app.models import TESTING, Submission
from app.poller import ingest

log = logging.getLogger("poller")

MAX_BURST_PAGES = 5
MAX_FINAL_PAGES = 40


class Poller:
    def __init__(self):
        self.watches: dict[int, Watch] = {}
        self.interactive_due: dict[int, int] = {}   # user_id -> when to run a lane-2 check
        self.last_hard_hint: dict[int, int] = {}
        self._in_flight: set[int] = set()
        self.listeners: list[Callable[[int, list[Submission]], object]] = []

    # ---------- what to watch ----------

    def watch(self, user_id: int, handle: str, start: int, end: int) -> None:
        now = clock.now()
        old = self.watches.get(user_id)
        w = Watch(user_id, handle, start, end, added_at=old.added_at if old else now,
                  last_checked=old.last_checked if old else 0,
                  last_activity=old.last_activity if old else None)
        self.watches[user_id] = w

    def unwatch(self, user_id: int) -> None:
        self.watches.pop(user_id, None)
        self.interactive_due.pop(user_id, None)

    # ---------- hints ----------

    def hard_hint(self, user_id: int) -> bool:
        """'I submitted'. Returns False if on cooldown or not watched."""
        now = clock.now()
        if user_id not in self.watches:
            return False
        if now - self.last_hard_hint.get(user_id, -10**9) < config.HARD_HINT_COOLDOWN_S:
            return False
        self.last_hard_hint[user_id] = now
        self._due_interactive(user_id, now + config.HARD_HINT_DELAY_S)
        self.watches[user_id].last_activity = now
        return True

    def soft_hint(self, user_id: int, away_s: float) -> None:
        """The player came back to our tab after being away: mark them HOT (no API call)."""
        w = self.watches.get(user_id)
        if w and away_s >= config.SOFT_HINT_MIN_AWAY_S:
            w.last_activity = max(w.last_activity or 0, clock.now())

    def _due_interactive(self, user_id: int, at: int) -> None:
        cur = self.interactive_due.get(user_id)
        self.interactive_due[user_id] = at if cur is None else min(cur, at)

    # ---------- checks ----------

    async def fetch(self, handle: str, lane: Lane, window_start: int, known_id: int,
                    first_count: int = config.POLL_COUNT, max_pages: int = MAX_BURST_PAGES) -> list[Submission]:
        """Newest submissions, paging back while every result is new and still inside the window."""
        cf = get_client()
        out: list[Submission] = []
        start, count = 1, first_count
        for _ in range(max_pages):
            raw = await cf.call("user.status", {"handle": handle, "from": start, "count": count}, lane=lane)
            subs = parse_submissions(raw, handle)
            out.extend(subs)
            if len(raw) < count:
                break
            oldest = min(s["creationTimeSeconds"] for s in raw)
            if oldest < window_start or any(s["id"] <= known_id for s in raw):
                break
            start += count
            count = config.POLL_PAGE_COUNT
        return out

    def _process(self, w: Watch, subs: list[Submission]) -> list[Submission]:
        changed = ingest.store(w.user_id, subs)
        in_window = [s for s in subs if w.window_start <= s.time < w.window_end]
        if in_window:
            newest = max(s.time for s in in_window)
            w.last_activity = max(w.last_activity or 0, newest)
        w.testing = any(s.verdict == TESTING for s in in_window)
        if w.testing:
            self._due_interactive(w.user_id, clock.now() + config.TESTING_RECHECK_S)
        if changed:
            for listener in self.listeners:
                try:
                    listener(w.user_id, changed)
                except Exception:
                    log.exception("poller listener failed")
        return changed

    def _known_id(self, user_id: int) -> int:
        return db.scalar("SELECT last_sub_id FROM users WHERE id = ?", (user_id,)) or 0

    async def check(self, user_id: int, lane: Lane = Lane.INTERACTIVE) -> list[Submission]:
        w = self.watches.get(user_id)
        if w is None:
            return []
        known = self._known_id(user_id)
        w.last_checked = clock.now()
        subs = await self.fetch(w.handle, lane, w.window_start, known)
        return self._process(w, subs)

    async def final_check(self, user_id: int, handle: str, start: int, end: int) -> bool:
        """Fetch everything back to `start`. Returns True when nothing in the window is TESTING.

        Raises CFError when Codeforces can't be reached.
        """
        cf = get_client()
        subs: list[Submission] = []
        offset = 1
        for _ in range(MAX_FINAL_PAGES):
            raw = await cf.call("user.status", {"handle": handle, "from": offset, "count": config.POLL_PAGE_COUNT},
                                lane=Lane.URGENT)
            subs.extend(parse_submissions(raw, handle))
            if len(raw) < config.POLL_PAGE_COUNT or min(s["creationTimeSeconds"] for s in raw) < start:
                break
            offset += config.POLL_PAGE_COUNT
        w = self.watches.get(user_id) or Watch(user_id, handle, start, end, added_at=clock.now())
        self._process(w, subs)
        return not any(s.verdict == TESTING and start <= s.time < end for s in subs)

    # ---------- loops ----------

    async def run(self) -> None:
        await asyncio.gather(self._poll_loop(), self._interactive_loop())

    async def _poll_loop(self) -> None:
        cf = get_client()
        while True:
            picked: list[Watch] = []

            def prepare():
                now = clock.now()
                w = schedule.pick(self.watches.values(), now)
                if w is None:
                    return None
                w.last_checked = now
                picked.append(w)
                return {"handle": w.handle, "from": 1, "count": config.POLL_COUNT}

            if schedule.pick(self.watches.values(), clock.now()) is None:
                await asyncio.sleep(1)
                continue
            try:
                raw = await cf.call("user.status", lane=Lane.POLL, prepare=prepare)
                if raw is None or not picked:
                    continue
                w = picked[0]
                subs = parse_submissions(raw, w.handle)
                if len(raw) == config.POLL_COUNT:
                    known = self._known_id(w.user_id)
                    if all(s["id"] > known for s in raw) and min(s["creationTimeSeconds"] for s in raw) >= w.window_start:
                        subs += await self.fetch(w.handle, Lane.POLL, w.window_start, known,
                                                 first_count=config.POLL_PAGE_COUNT)
                if w.user_id in self.watches:
                    self._process(w, subs)
            except CFError as e:
                log.warning("poll failed: %s", e)
                await asyncio.sleep(1)
            except Exception:
                log.exception("poll loop error")
                await asyncio.sleep(1)

    async def _interactive_loop(self) -> None:
        while True:
            now = clock.now()
            for user_id, at in list(self.interactive_due.items()):
                if at <= now and user_id not in self._in_flight:
                    del self.interactive_due[user_id]
                    self._in_flight.add(user_id)
                    asyncio.create_task(self._interactive_check(user_id))
            await asyncio.sleep(1)

    async def _interactive_check(self, user_id: int) -> None:
        try:
            await self.check(user_id, Lane.INTERACTIVE)
        except CFError as e:
            log.warning("interactive check failed: %s", e)
        except Exception:
            log.exception("interactive check error")
        finally:
            self._in_flight.discard(user_id)


poller = Poller()
