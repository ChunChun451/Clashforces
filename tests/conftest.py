import json
from pathlib import Path

import httpx
import pytest

from app import db

FIXTURES = Path(__file__).with_name("fixtures")


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


class FakeClock:
    """A clock that only moves when sleep() is called."""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += max(0.0, seconds)


class FakeCF:
    """httpx transport that answers from a list of canned responses per method."""

    def __init__(self):
        self.routes: dict[str, list] = {}
        self.calls: list[tuple[str, dict]] = []

    def add(self, method: str, *responses) -> None:
        self.routes.setdefault(method, []).extend(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        self.calls.append((method, dict(request.url.params)))
        queue = self.routes.get(method) or []
        if not queue:
            return httpx.Response(500, text="no fake response")
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        if isinstance(item, httpx.Response):
            return item
        status = 200 if item.get("status") == "OK" else 400
        return httpx.Response(status, json=item)

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def fresh_db(tmp_path):
    conn = db.connect(str(tmp_path / "test.db"))
    yield conn
    conn.close()
    db._conn = None
