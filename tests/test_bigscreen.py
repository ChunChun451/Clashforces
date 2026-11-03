"""The big screen (/m/CODE/screen): always the spectator view, never frozen results or hidden problems."""

import base64
import json

import pytest
from itsdangerous import TimestampSigner

from app import config, db
from app.matches import contest, events, service, store, view
from app.poller.poller import poller
from app.ws.hub import hub
from tests.test_contest import HANDLES, KEYS, T0, form, no_leak, raw, settle, world  # noqa: F401

A, B, C = KEYS


def cookie(user) -> str:
    sv = db.scalar("SELECT session_version FROM users WHERE id = ?", (user["id"],))
    data = base64.b64encode(json.dumps({"uid": user["id"], "sv": sv}).encode())
    return "cf_session=" + TimestampSigner(str(config.SECRET_KEY)).sign(data).decode()


def subscribe(client, code, path="/ws", user=None):
    headers = {"cookie": cookie(user)} if user else {}
    ws = client.websocket_connect(path, headers=headers)
    sock = ws.__enter__()
    sock.receive_json()
    sock.send_json({"type": "subscribe", "data": {"code": code}})
    msg = sock.receive_json()
    assert msg["type"] == "match.state"
    return ws, msg["data"]


def test_page_is_static_and_public(client, world):
    w = world
    code = contest.create(w.u["host"], form(visibility="unlisted"))
    client.cookies.clear()
    r = client.get(f"/m/{code}/screen")
    assert r.status_code == 200 and "page-screen.js" in r.text
    no_leak(r.text)
    assert client.get(f"/m/{code.lower()}/screen").status_code == 200


def test_before_the_start_nothing_about_problems(client, world):
    w = world
    code = contest.create(w.u["host"], form())
    for h in HANDLES[:3]:
        service.join(w.u[h], code)
    client.cookies.clear()
    # anonymous, a player and even the host get nothing about the problems on a screen socket
    for user in (None, w.u["c0"], w.u["host"]):
        ws, st = subscribe(client, code, "/ws?view=screen", user)
        ws.__exit__(None, None, None)
        assert st["problems"] == [] and "host" not in st and st["viewer_id"] is None
        no_leak(st)


async def test_countdown_keeps_problems_hidden(world):
    w = world
    code = contest.create(w.u["host"], form(scheduled_at=None))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    assert m["start_time"] > w.now.t                       # 5 s countdown: running, problems not shown yet
    st = view.build(m, None)
    assert st["status"] == "running" and st["problems"] == [] and st["standings"] == []
    no_leak(st)
    w.now.t = m["start_time"]
    assert [p["label"] for p in view.build(m, None)["problems"]] == ["A", "B", "C"]


async def frozen_running(w):
    code = contest.create(w.u["host"], form(scheduled_at=None, freeze_min=30))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    fs = view.freeze_start(m, store.settings(m))
    w.subs["c0"] += [raw(1, A, "OK", m["start_time"] + 600)]          # Owls, visible to all
    w.subs["c1"] += [raw(2, B, "OK", fs + 60)]                        # Team B, frozen
    w.now.t = fs + 120
    for h in ("c0", "c1"):
        await poller.check(w.u[h]["id"])
    return store.match_by_id(m["id"])


def by_name(st):
    return {r["name"]: r for r in st["standings"]}


async def test_screen_socket_is_a_spectator_even_for_a_player(client, world):
    w = world
    m = await frozen_running(w)
    client.cookies.clear()
    ws, mine = subscribe(client, m["code"], "/ws", w.u["c1"])                 # c1's own match page
    ws.__exit__(None, None, None)
    assert by_name(mine)["Team B"]["solved"] == 1                            # their team, live
    ws, screen = subscribe(client, m["code"], "/ws?view=screen", w.u["c1"])  # c1's laptop on the projector
    ws.__exit__(None, None, None)
    assert screen["frozen"] and screen["me"] is None and screen.get("my_team") is None
    assert by_name(screen)["Team B"]["solved"] == 0
    assert by_name(screen)["Team B"]["cells"][1]["state"] == "frozen"
    assert by_name(screen)["Owls"]["solved"] == 1


async def test_live_updates_to_a_screen_hide_frozen_results(world, monkeypatch):
    w = world
    m = await frozen_running(w)
    sent = []
    monkeypatch.setattr(hub, "_spawn", lambda conn, msg: sent.append((conn, msg)))

    class Conn:
        def __init__(self, uid, screen):
            self.user_id, self.screen, self.matches = uid, screen, set()

        @property
        def viewer(self):
            return None if self.screen else self.user_id
    screen, page = Conn(w.u["c4"]["id"], True), Conn(w.u["c4"]["id"], False)
    hub.subscribe(screen, m["code"])
    hub.subscribe(page, m["code"])
    try:
        w.subs["c4"] += [raw(3, C, "OK", w.now.t - 10)]                      # frozen solve by Team B
        await poller.check(w.u["c4"]["id"])
        await settle()
        got = {c: msg["data"] for c, msg in sent if msg["type"] == "standings"}
        assert got[screen]["changed"] == [] and got[screen]["frozen"]        # no toast material for the screen
        assert by_name(got[screen])["Team B"]["solved"] == 0
        assert got[page]["changed"] and by_name(got[page])["Team B"]["solved"] == 2
        no_frozen_ok = [c for c in by_name(got[screen])["Team B"]["cells"] if c["state"] == "ok"]
        assert no_frozen_ok == []
        # a pre-freeze solve does reach the screen, so it can show a first-solve toast
        sent.clear()
        events.submissions(m["id"], w.u["c0"]["id"], [])
        assert all(msg["data"]["frozen"] for c, msg in sent if c is screen)
    finally:
        hub.by_match.clear()


async def test_first_solves_before_the_freeze_are_visible(world):
    w = world
    m = await frozen_running(w)
    st = view.build(m, None)
    a = by_name(st)["Owls"]["cells"][0]
    assert a["state"] == "ok" and a["by_handle"] == "c0" and a["minute"] == 10 and "time" in a


def test_any_match_with_its_code_is_watchable(client, world):
    """Private (non-contest) matches: same rule as the match page, the code is the key."""
    w = world
    code = service.create(w.u["c0"], "friend", "icpc", {"count": 2, "rating_min": 800, "rating_max": 1200,
                                                        "duration_min": 30})
    client.cookies.clear()
    assert client.get(f"/m/{code}/screen").status_code == 200
    ws, st = subscribe(client, code, "/ws?view=screen")
    ws.__exit__(None, None, None)
    assert st["code"] == code and st["mode"] == "friend"
    ws = client.websocket_connect("/ws?view=screen")
    with ws as sock:
        sock.receive_json()
        sock.send_json({"type": "subscribe", "data": {"code": "NOPE00"}})
        assert sock.receive_json()["type"] == "error"


@pytest.mark.parametrize("rules", ["icpc", "lockout"])
async def test_solo_contest_screen_lists_players(world, rules):
    w = world
    code = contest.create(w.u["host"], form(format="solo", rules=rules, max_players=40, scheduled_at=None,
                                            freeze_min=0))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    w.now.t = m["start_time"] + 60
    st = view.build(m, None)
    assert len(st["standings"]) == len(HANDLES) and "teams" not in st
    assert {r["handle"] for r in st["standings"]} == set(HANDLES)
