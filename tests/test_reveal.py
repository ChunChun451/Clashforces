"""The ICPC-style freeze reveal of hosted contests: ordering, ranking, locking, host controls, sync."""

import asyncio
import base64
import json

import pytest
from itsdangerous import TimestampSigner

from app import config, db
from app.core.freeze import reveal_steps, reveal_targets, split_visible
from app.core.icpc import score_icpc
from app.core.lockout import score_lockout
from app.matches import contest, events, finalize, lifecycle, reveal, service, stats_data, store, view
from app.models import PlayerInput, Sub
from app.poller.poller import poller
from app.ws.hub import hub
from tests.test_contest import HANDLES, KEYS, T0, form, raw, settle, world  # noqa: F401  (world is a fixture)

A, B, C = KEYS


# ---------- the order itself (pure) ----------

def _players(*ids, end=3600):
    return [PlayerInput(i, end, ("A", "B", "C")) for i in ids]


SUBS = [
    Sub(1, "x", "A", "OK", 600),            # before the freeze
    Sub(2, "y", "A", "OK", 900),
    Sub(3, "z", "A", "WRONG_ANSWER", 300),
    Sub(4, "z", "A", "OK", 3060),           # frozen from here on
    Sub(5, "z", "B", "OK", 3120),
    Sub(6, "y", "B", "WRONG_ANSWER", 3100),
    Sub(7, "x", "C", "OK", 3300),
]
ORDER = {p: ("A", "B", "C") for p in "xyz"}


def test_targets_icpc_last_place_first_then_upward():
    score = lambda s: score_icpc(0, _players("x", "y", "z"), s)  # noqa: E731
    targets = reveal_targets(score, ORDER, list(SUBS), 3000)
    # z is last: its A then B (z climbs to first); then y (now last) B; then x's C
    assert targets == [("z", "A"), ("z", "B"), ("y", "B"), ("x", "C")]
    # same order as the step-by-step version, and the last board is the real final board
    steps = reveal_steps(score, ORDER, list(SUBS), 3000)
    assert [(s["player"], s["problem"]) for s in steps] == targets
    final = score(list(SUBS))
    assert [(r.player, r.place, r.solved, r.penalty) for r in steps[-1]["rows"]] == \
        [(r.player, r.place, r.solved, r.penalty) for r in final]
    assert [r.player for r in final] == ["x", "z", "y"]


def test_targets_lockout_end_on_the_true_final_ranking():
    pts = {"A": 100, "B": 200, "C": 300}
    score = lambda s: score_lockout(0, _players("x", "y", "z"), pts, s)  # noqa: E731
    targets = reveal_targets(score, ORDER, list(SUBS), 3000)
    assert targets[0][0] == "z" and len(targets) == 4          # z (0 points) is revealed first
    visible, hidden = split_visible(list(SUBS), None, 3000)
    shown = list(visible)
    for t in targets:
        shown += [h for h in hidden if (h.player, h.problem) == t]
    final = score(list(SUBS))
    assert [(r.player, r.points, r.place) for r in score(shown)] == [(r.player, r.points, r.place) for r in final]
    assert [r.player for r in final] == ["x", "z", "y"]         # 400 > 200 (z: B) > 100 (y: A)


def test_no_freeze_or_nothing_hidden_means_no_steps():
    score = lambda s: score_icpc(0, _players("x", "y"), s)  # noqa: E731
    assert reveal_targets(score, ORDER, list(SUBS[:2]), 3000) == []
    assert reveal_targets(score, ORDER, list(SUBS), None) == []


# ---------- a 3 × 3 team contest with a freeze ----------

def login(client, user):
    sv = db.scalar("SELECT session_version FROM users WHERE id = ?", (user["id"],))
    data = base64.b64encode(json.dumps({"uid": user["id"], "sv": sv}).encode())
    client.cookies.set("cf_session", TimestampSigner(str(config.SECRET_KEY)).sign(data).decode())


async def finish(w, match):
    for _ in range(10):
        await lifecycle.tick()
        await settle()
        if store.match_by_id(match["id"])["result_status"] == "final":
            break
    return store.match_by_id(match["id"])


async def frozen_contest(w, **kw):
    """Owls (c0 c3 c6), Team B (c1 c4 c7), Foxes (c2 c5 c8); ends with 4 frozen cells to reveal."""
    code = contest.create(w.u["host"], form(scheduled_at=None, freeze_min=30, **kw))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    start, fs = m["start_time"], view.freeze_start(m, store.settings(m))
    w.subs["c0"] += [raw(1, A, "OK", start + 600)]                  # Owls A, 10'
    w.subs["c1"] += [raw(2, A, "OK", start + 1200)]                 # Team B A, 20'
    w.subs["c2"] += [raw(3, A, "WRONG_ANSWER", start + 300),        # Foxes: -1 on A, then everything frozen
                     raw(4, A, "OK", fs + 60)]
    w.subs["c5"] += [raw(5, B, "OK", fs + 120)]
    w.subs["c4"] += [raw(6, B, "WRONG_ANSWER", fs + 100)]           # Team B: a frozen wrong try
    w.subs["c3"] += [raw(7, C, "OK", fs + 300)]                     # Owls C
    w.now.t = fs + 400
    for h in HANDLES:
        await poller.check(w.u[h]["id"])
    await settle()
    w.now.t = fs + 500
    contest.end_early(w.u["host"], code)
    w.now.t = fs + 600
    m = await finish(w, store.match_by_code(code))
    assert m["result_status"] == "final"
    return m


def solved(rows):
    return {r["name"]: r["solved"] for r in rows}


async def test_contest_with_freeze_waits_for_the_reveal(client, world):
    w = world
    m = await frozen_contest(w)
    code = m["code"]
    assert reveal.locked(m) and reveal.status(m) == "waiting"
    # nobody sees the final board yet: spectators and the host see it frozen ...
    for viewer in (None, w.u["host"]["id"]):
        st = view.build(m, viewer)
        assert st["frozen"] and st["reveal"]["status"] == "waiting" and st["reveal"]["total"] == 4
        assert solved(st["standings"]) == {"Owls": 1, "Team B": 1, "Foxes": 0}
    # ... and each team sees only itself live
    foxes = view.build(m, w.u["c2"]["id"])
    assert solved(foxes["standings"]) == {"Owls": 1, "Team B": 1, "Foxes": 2}
    # the result, timeline, Events winner and profile history stay locked
    client.cookies.clear()
    assert client.get(f"/api/matches/{code}/result").status_code == 409
    assert client.get(f"/api/matches/{code}/timeline").status_code == 409
    past = client.get("/api/events").json()["past"]
    assert [p["winner"] for p in past if p["code"] == code] == [None]
    assert stats_data.recent_matches(w.u["c0"]["id"]) == []
    r = client.get("/api/users/c0/matches")
    assert r.status_code == 200 and r.json()["matches"] == []
    assert client.get("/api/users/c0/stats").json()["matches_played"] == 0


async def test_reveal_steps_rank_and_unlock(client, world):
    w = world
    m = await frozen_contest(w)
    code = m["code"]
    with pytest.raises(service.MatchError, match="host"):
        reveal.control(w.u["c0"], code, "next")
    with pytest.raises(service.MatchError, match="Unknown"):
        reveal.control(w.u["host"], code, "dance")
    login(client, w.u["c0"])
    assert client.post(f"/api/contests/{code}/reveal", json={"action": "start"}).status_code == 400

    info = reveal.control(w.u["host"], code, "start")
    assert info["status"] == "running" and info["step"] == 0
    assert info["next"] == {"row": "t2", "label": "A"}                # last place first: Foxes, leftmost
    m = store.match_by_code(code)
    # while it runs, players see the shared board too (Foxes' own cells are pending again)
    for viewer in (None, w.u["c2"]["id"], w.u["host"]["id"]):
        st = view.build(m, viewer)
        assert st["frozen"] and solved(st["standings"]) == {"Owls": 1, "Team B": 1, "Foxes": 0}

    seen = []
    order = []
    for _ in range(4):
        info = reveal.control(w.u["host"], code, "next")
        order.append((info["last"] or {}).get("row"))
        m = store.match_by_code(code)
        st = view.build(m, None)
        seen.append([(r["name"], r["place"], r["solved"]) for r in st["standings"]])
    assert seen[0] == [("Owls", 1, 1), ("Team B", 2, 1), ("Foxes", 3, 1)]       # Foxes A: +1, still last
    assert seen[1] == [("Foxes", 1, 2), ("Owls", 2, 1), ("Team B", 3, 1)]       # Foxes B: jumps to first
    assert seen[2] == [("Foxes", 1, 2), ("Owls", 2, 1), ("Team B", 3, 1)]       # Team B's frozen B was wrong
    assert order[:3] == ["t2", "t2", "t1"]
    # the last step (Owls C) ends the reveal and unlocks everything
    assert info["status"] == "done" and info["step"] == 4
    m = store.match_by_code(code)
    assert not reveal.locked(m)
    final = view.build(m, None)
    assert not final["frozen"] and final["standings"][0]["name"] == "Owls"
    assert [(r["name"], r["place"]) for r in final["standings"]] == [("Owls", 1), ("Foxes", 2), ("Team B", 3)]
    client.cookies.clear()
    res = client.get(f"/api/matches/{code}/result")
    assert res.status_code == 200 and res.json()["rows"][0]["name"] == "Owls"
    assert client.get(f"/api/matches/{code}/timeline").status_code == 200
    assert [p["winner"] for p in client.get("/api/events").json()["past"] if p["code"] == code] == \
        [{"kind": "team", "names": ["Owls"]}]
    assert [x["code"] for x in stats_data.recent_matches(w.u["c0"]["id"])] == [code]
    with pytest.raises(service.MatchError, match="nothing left"):
        reveal.control(w.u["host"], code, "next")


async def test_skip_to_final_and_frames_are_broadcast(world):
    w = world
    m = await frozen_contest(w)
    sent = []
    old = hub._spawn
    hub._spawn = lambda conn, msg: sent.append((conn.viewer, msg))

    class FakeConn:
        def __init__(self, uid, screen=False):
            self.user_id, self.screen, self.matches = uid, screen, set()

        @property
        def viewer(self):
            return None if self.screen else self.user_id
    try:
        for c in (FakeConn(None), FakeConn(w.u["c2"]["id"], screen=True), FakeConn(w.u["c1"]["id"])):
            hub.subscribe(c, m["code"])
        reveal.control(w.u["host"], m["code"], "next")
        frames = [msg for _, msg in sent if msg["type"] == "match.reveal"]
        assert len(frames) == 3 and all(f["data"] == frames[0]["data"] for f in frames)  # one board for all
        assert frames[0]["data"]["reveal"]["status"] == "running"
        assert {msg["type"] for _, msg in sent} == {"match.reveal", "match.state"}     # status change: full state too
        sent.clear()
        info = reveal.control(w.u["host"], m["code"], "skip")
        assert info["status"] == "done"
        last = [msg for _, msg in sent if msg["type"] == "match.reveal"][0]["data"]
        assert last["reveal"]["status"] == "done" and last["standings"][0]["name"] == "Owls"
    finally:
        hub._spawn = old
        hub.by_match.clear()
    assert not reveal.locked(store.match_by_id(m["id"]))


async def test_auto_play_runs_to_the_end(world, monkeypatch):
    w = world
    m = await frozen_contest(w)
    monkeypatch.setattr(config, "REVEAL_AUTO_S", 0)
    info = reveal.control(w.u["host"], m["code"], "auto")
    assert info["auto"] and info["status"] == "running"
    for _ in range(50):
        await asyncio.sleep(0)
        if reveal.status(store.match_by_id(m["id"])) == "done":
            break
    assert reveal.status(store.match_by_id(m["id"])) == "done"
    assert m["code"] not in reveal._auto


async def test_pause_stops_auto_play(world, monkeypatch):
    w = world
    m = await frozen_contest(w)
    monkeypatch.setattr(config, "REVEAL_AUTO_S", 3600)
    reveal.control(w.u["host"], m["code"], "auto")
    info = reveal.control(w.u["host"], m["code"], "pause")
    assert not info["auto"] and info["status"] == "running" and info["step"] == 0
    await settle()


async def test_reconnecting_screen_gets_the_current_step(client, world):
    w = world
    m = await frozen_contest(w)
    reveal.control(w.u["host"], m["code"], "start")
    reveal.control(w.u["host"], m["code"], "next")
    reveal.control(w.u["host"], m["code"], "next")
    login(client, w.u["c1"])                       # a player, on a big screen and on the match page
    for path in ("/ws?view=screen", "/ws"):
        with client.websocket_connect(path) as ws:
            ws.receive_json()
            ws.send_json({"type": "subscribe", "data": {"code": m["code"]}})
            st = ws.receive_json()["data"]
            assert st["reveal"]["status"] == "running" and st["reveal"]["step"] == 2
            assert st["reveal"]["last"] == {"row": "t2", "label": "B"}
            assert [(r["name"], r["solved"]) for r in st["standings"]] == [("Foxes", 2), ("Owls", 1), ("Team B", 1)]


async def test_no_lock_without_frozen_submissions_or_freeze(world):
    w = world
    # a freeze, but nobody submitted during it: the results show right away
    code = contest.create(w.u["host"], form(scheduled_at=None, freeze_min=30))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    w.subs["c0"] += [raw(1, A, "OK", m["start_time"] + 60)]
    w.now.t = m["start_time"] + 120
    await poller.check(w.u["c0"]["id"])
    contest.end_early(w.u["host"], code)
    w.now.t += 60
    m = await finish(w, m)
    assert reveal.status(m) is None and not reveal.locked(m)
    assert not view.build(m, None)["frozen"] and "reveal" not in view.build(m, None)
    with pytest.raises(service.MatchError, match="nothing left"):
        reveal.control(w.u["host"], code, "start")


async def test_lockout_contest_never_locks(world):
    w = world
    code = contest.create(w.u["host"], form(scheduled_at=None, rules="lockout", freeze_min=30))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    w.subs["c2"] += [raw(1, A, "OK", m["start_time"] + 60)]
    w.now.t = m["start_time"] + 120
    await poller.check(w.u["c2"]["id"])
    contest.end_early(w.u["host"], code)
    w.now.t += 60
    m = await finish(w, m)
    assert m["result_status"] == "final" and not reveal.locked(m)
    assert view.build(m, None)["standings"][0]["name"] == "Foxes"


async def test_reveal_needs_a_final_result(world):
    w = world
    code = contest.create(w.u["host"], form(scheduled_at=None))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    with pytest.raises(service.MatchError, match="over"):
        reveal.control(w.u["host"], code, "start")
    contest.end_early(w.u["host"], code)
    with pytest.raises(service.MatchError, match="confirm"):
        reveal.control(w.u["host"], code, "start")


async def test_locked_result_is_not_broadcast(world):
    w = world
    m = await frozen_contest(w)
    sent = []
    old = hub._spawn
    hub._spawn = lambda conn, msg: sent.append(msg["type"])

    class FakeConn:
        user_id, screen, viewer, matches = None, False, None, set()
    try:
        hub.subscribe(FakeConn(), m["code"])
        events.result(m["id"])
        assert "match.result" not in sent and "match.state" in sent
    finally:
        hub._spawn = old
        hub.by_match.clear()


def test_finalize_hook_is_registered():
    assert reveal.on_final in finalize.before_broadcast
