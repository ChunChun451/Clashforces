"""Regression tests for bugs found in review."""

import asyncio
import random

import pytest

from app import config, db
from app.cf.client import CFError
from app.matches import lifecycle, service, store
from app.routes import ws as ws_routes
from app.ws.hub import Conn, Hub
from tests.conftest import FakeCF, FakeClock, load
from tests.test_cf_client import drain, make
from tests.test_match_flow import finish, raw, start_duel, world  # noqa: F401  (fixture)


# ---------- 1. start() never leaves a match in 'starting' ----------

async def test_unexpected_error_during_start_returns_to_lobby(world, monkeypatch):  # noqa: F811
    w = world
    code = service.create(w.a, "friend", "icpc", {"count": 3, "rating_min": 1400, "rating_max": 1800,
                                                  "duration_min": 60})
    service.join(w.b, code)
    match = store.match_by_code(code)
    db.execute("UPDATE match_players SET ready = 1 WHERE match_id = ?", (match["id"],))

    def boom(*a, **k):
        raise TypeError("bug in selection")
    monkeypatch.setattr(service, "choose_problems", boom)
    assert await service.start(match["id"], random.Random(1)) is False
    m = store.match_by_id(match["id"])
    assert m["status"] == "lobby"
    assert not any(p["ready"] for p in store.players(match["id"]))


async def test_spawned_task_errors_are_logged(caplog):
    async def bad():
        raise ValueError("oops")
    service._spawn(bad())
    for _ in range(3):
        await asyncio.sleep(0)
    assert any("background task" in r.message and r.exc_info for r in caplog.records)


# ---------- 2. default secret key refused in production ----------

def test_default_secret_rejected_with_secure_cookies(monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "SECRET_KEY", config.DEFAULT_SECRET_KEY)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.check_production_settings()


def test_custom_secret_or_dev_mode_ok(monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "something-long-and-random")
    config.check_production_settings()
    monkeypatch.setattr(config, "COOKIE_SECURE", False)
    monkeypatch.setattr(config, "SECRET_KEY", config.DEFAULT_SECRET_KEY)
    config.check_production_settings()


# ---------- 3. websocket origin check ----------

def test_origin_check(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", [])
    assert ws_routes.origin_allowed(None, "localhost:8000")
    assert ws_routes.origin_allowed("http://localhost:8000", "localhost:8000")
    assert not ws_routes.origin_allowed("https://evil.example", "localhost:8000")
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", ["https://duel.example.com"])
    assert ws_routes.origin_allowed("https://duel.example.com/", "internal:8000")
    assert not ws_routes.origin_allowed("http://localhost:8000", "localhost:8000")


def test_ws_rejects_foreign_origin(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setattr(config, "ALLOWED_ORIGINS", [])
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
    assert e.value.code == 1008
    with client.websocket_connect("/ws", headers={"origin": "http://testserver"}) as ws:
        assert ws.receive_json()["type"] == "hello"


# ---------- 4. CF client never leaves a caller hanging ----------

async def test_unexpected_error_in_request_resolves_future(monkeypatch):
    clock, fake = FakeClock(), FakeCF()
    fake.add("user.info", load("user_info_ok.json"))
    client = make(fake, clock)

    async def broken(job):
        raise KeyError("result")
    monkeypatch.setattr(client, "_request", broken)
    task = asyncio.create_task(client.call("user.info", {"handles": "alice"}))
    with pytest.raises(CFError):
        await drain(client, task)


# ---------- 5. smaller fixes ----------

async def test_timeline_skips_submission_of_unknown_player(world, monkeypatch):  # noqa: F811
    from app.routes import matches as match_routes
    w = world
    match = await start_duel(w)
    k = store.problems(match["id"])[0]["problem_key"]
    w.subs["Bob_77"].append(raw(1, k, "OK", match["start_time"] + 60))
    w.now.t = match["end_time"] + 30
    m = await finish(w, match)
    assert m["result_status"] == "final"

    real_subs = store.subs

    def subs_with_ghost(match, pr, pb):
        out = list(real_subs(match, pr, pb))
        ghost = type(out[0])(**{**out[0].__dict__, "player": 999_999}) if out else None
        return out + ([ghost] if ghost else [])
    monkeypatch.setattr(store, "subs", subs_with_ghost)
    res = await match_routes.timeline(m["code"], user=None)
    assert all(i["user_id"] != 999_999 for i in res["items"])
    assert len(res["items"]) == 1


def test_send_all_reaches_every_socket_once():
    hub = Hub()
    sent = []
    hub._spawn = lambda conn, msg: sent.append(conn)
    anon, user = Conn(None, None), Conn(None, 1)
    hub.add(anon)
    hub.add(user)
    hub.subscribe(anon, "ABC")
    hub.subscribe(user, "ABC")
    hub.send_all("api.status", {"cf_api": "down"})
    assert sorted(map(id, sent)) == sorted([id(anon), id(user)])
    hub.remove(anon)
    hub.remove(user)
    assert not hub.conns


def test_rate_limit_forgets_old_ips(monkeypatch):
    from app.routes import auth
    t = [10_000.0]
    monkeypatch.setattr(auth.time, "time", lambda: t[0])
    monkeypatch.setattr(auth, "_last_sweep", 0.0)
    auth._starts.clear()
    auth._rate_limit("1.1.1.1")
    t[0] += 3600
    auth._rate_limit("2.2.2.2")
    assert "1.1.1.1" not in auth._starts and "2.2.2.2" in auth._starts


def test_head_root(client):
    assert client.head("/").status_code == 200
