"""Live connections: who is watching which match, and who is online.

Messages are {"type": ..., "data": {...}}. Match messages can be built per
viewer (freeze makes the scoreboard different for each player).
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger("hub")


class Conn:
    def __init__(self, ws, user_id: int | None):
        self.ws = ws
        self.user_id = user_id
        self.matches: set[str] = set()

    async def send(self, msg: dict) -> None:
        try:
            await self.ws.send_json(msg)
        except Exception:
            pass  # the socket's own loop will notice and clean up


class Hub:
    def __init__(self):
        self.conns: set[Conn] = set()  # every open socket, logged in or not
        self.by_match: dict[str, set[Conn]] = defaultdict(set)
        self.by_user: dict[int, set[Conn]] = defaultdict(set)
        self.presence_listeners: list[Callable[[int, bool], Any]] = []

    # ---------- connections ----------

    def add(self, conn: Conn) -> None:
        self.conns.add(conn)
        if conn.user_id is not None:
            first = not self.by_user[conn.user_id]
            self.by_user[conn.user_id].add(conn)
            if first:
                self._presence(conn.user_id, True)

    def remove(self, conn: Conn) -> None:
        self.conns.discard(conn)
        for code in list(conn.matches):
            self.unsubscribe(conn, code)
        if conn.user_id is not None:
            conns = self.by_user.get(conn.user_id)
            if conns is not None:
                conns.discard(conn)
                if not conns:
                    del self.by_user[conn.user_id]
                    self._presence(conn.user_id, False)

    def subscribe(self, conn: Conn, code: str) -> None:
        conn.matches.add(code)
        self.by_match[code].add(conn)

    def unsubscribe(self, conn: Conn, code: str) -> None:
        conn.matches.discard(code)
        subs = self.by_match.get(code)
        if subs is not None:
            subs.discard(conn)
            if not subs:
                del self.by_match[code]

    def online(self, user_id: int) -> bool:
        return bool(self.by_user.get(user_id))

    def _presence(self, user_id: int, online: bool) -> None:
        for fn in self.presence_listeners:
            try:
                fn(user_id, online)
            except Exception:
                log.exception("presence listener failed")

    # ---------- sending ----------

    def _spawn(self, conn: Conn, msg: dict) -> None:
        try:
            asyncio.get_running_loop().create_task(conn.send(msg))
        except RuntimeError:
            pass  # no loop (sync tests)

    def send_all(self, type_: str, data: dict) -> None:
        for conn in list(self.conns):
            self._spawn(conn, {"type": type_, "data": data})

    def send_user(self, user_id: int, type_: str, data: dict) -> None:
        for conn in list(self.by_user.get(user_id, ())):
            self._spawn(conn, {"type": type_, "data": data})

    def send_match(self, code: str, type_: str, data: dict | Callable[[int | None], dict | None]) -> None:
        """data may be a function of the viewer's user id (None = spectator); results are cached per viewer."""
        cache: dict = {}
        for conn in list(self.by_match.get(code, ())):
            if callable(data):
                if conn.user_id not in cache:
                    cache[conn.user_id] = data(conn.user_id)
                payload = cache[conn.user_id]
            else:
                payload = data
            if payload is not None:
                self._spawn(conn, {"type": type_, "data": payload})


hub = Hub()
