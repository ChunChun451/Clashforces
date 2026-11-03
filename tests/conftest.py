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


class StubCF:
    """Stands in for CFClient in service tests. Handlers map method -> fn(params)."""

    def __init__(self):
        self.handlers = {}
        self.calls = []
        self.down = False

    def on(self, method, fn):
        self.handlers[method] = fn

    async def call(self, method, params=None, lane=None, timeout=None, prepare=None):
        if prepare is not None:
            params = prepare()
            if params is None:
                return None
        self.calls.append((method, params, lane))
        result = self.handlers[method](params or {})
        if isinstance(result, Exception):
            raise result
        return result

    def queue_depths(self):
        return {}


@pytest.fixture
def stub_cf():
    from app.cf.client import set_client
    stub = StubCF()
    set_client(stub)
    yield stub
    set_client(None)


@pytest.fixture
def frozen_now(monkeypatch):
    """Control app.clock.now(): set frozen_now.t = ..."""
    from app import clock

    class Now:
        t = 1_750_000_000

    monkeypatch.setattr(clock, "now", lambda: Now.t)
    return Now


@pytest.fixture
def cache_filled(fresh_db):
    from app.cf import cache
    from app.cf.parse import parse_contests, parse_problems
    cache.store_contests(parse_contests(load("contest_list.json")["result"]))
    cache.store_problems(parse_problems(load("problemset.json")["result"]))
    return cache


@pytest.fixture
def client(tmp_path, monkeypatch, stub_cf):
    """A TestClient with no background tasks and a temp database."""
    from fastapi.testclient import TestClient

    from app import config
    monkeypatch.setattr(config, "BACKGROUND_TASKS", False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "api.db"))
    from app.main import app
    with TestClient(app, headers={"X-Requested-With": "fetch"}) as c:
        yield c
    db._conn = None
