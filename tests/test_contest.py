"""Hosted contests: hidden problems, scheduled start, host controls, announcements, the Events tab."""

import asyncio
import base64
import json
import sqlite3

import pytest
from itsdangerous import TimestampSigner

from app import config, db
from app.auth import users
from app.matches import contest, lifecycle, service, store, view
from app.poller.poller import poller

T0 = 1_750_000_000
HANDLES = [f"c{i}" for i in range(9)]
PROBLEMS = "2000C https://codeforces.com/contest/2001/problem/B 1950C"
KEYS = ("2000C", "2001B", "1950C")


@pytest.fixture
def world(cache_filled, stub_cf, frozen_now):
    poller.watches.clear()
    lifecycle._sent.clear()
    lifecycle._final_next.clear()
    lifecycle._final_busy.clear()
    contest._retry_at.clear()
    contest._prefetched.clear()
    contest._starting.clear()
    lifecycle.setup()
    frozen_now.t = T0
    for i, h in enumerate(["host"] + HANDLES):
        users.create(h, 1300 + 50 * i, 1900)
    db.execute("UPDATE users SET history_synced = 1")
    subs = {h: [] for h in ["host"] + HANDLES}
    stub_cf.on("user.info", lambda p: [{"handle": h, "rating": 1500, "maxRating": 2500}
                                       for h in p["handles"].split(";")])
    stub_cf.on("user.status", lambda p: list(reversed(subs[p["handle"]])))

    class W:
        pass
    w = W()
    w.u = {h: users.by_handle(h) for h in ["host"] + HANDLES}
    w.subs, w.cf, w.now = subs, stub_cf, frozen_now
    return w


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


def raw(i, key, verdict, t):
    return {"id": i, "creationTimeSeconds": t, "verdict": verdict,
            "problem": {"contestId": int(key[:-1]), "index": key[-1]}}


def form(**kw):
    f = {"title": "Friday Night Clash", "format": "teams", "rules": "icpc", "duration_min": 120, "freeze_min": 30,
         "team_count": 3, "team_size": 3, "visibility": "public", "scheduled_at": T0 + 3600,
         "problems": PROBLEMS, "team_names": ["Owls", "", "Foxes"]}
    f.update(kw)
    return f


def no_leak(payload) -> None:
    text = json.dumps(payload)
    for k in KEYS:
        assert k not in text, f"{k} leaked"
    for name in ("contest_problems",):
        assert name not in text


# ---------- creating ----------

async def test_create_validates_and_stores_teams(world, monkeypatch):
    w = world
    with pytest.raises(service.MatchError, match="title"):
        contest.create(w.u["host"], form(title="  "))
    with pytest.raises(service.MatchError, match="past"):
        contest.create(w.u["host"], form(scheduled_at=T0 - 3600))
    monkeypatch.setattr(config, "CONTEST_PROBLEMS_MAX", 2)
    with pytest.raises(service.MatchError, match="At most 2"):
        contest.create(w.u["host"], form())
    monkeypatch.setattr(config, "CONTEST_PROBLEMS_MAX", 15)
    with pytest.raises(service.MatchError, match="Two teams"):
        contest.create(w.u["host"], form(team_names=["Owls", "owls"]))
    code = contest.create(w.u["host"], form(rules="lockout", points=[50, "", 500]))
    m = store.match_by_code(code)
    assert m["mode"] == "contest" and m["visibility"] == "public" and m["scheduled_at"] == T0 + 3600
    s = store.settings(m)
    assert [(p["label"], p["key"], p["points"]) for p in s["contest_problems"]] == \
        [("A", "2000C", 50), ("B", "2001B", 200), ("C", "1950C", 500)]
    assert store.team_names(m) == ["Owls", "Team B", "Foxes"]
    assert store.players(m["id"]) == []                     # the host is not a player
    with pytest.raises(service.MatchError, match="hosting"):
        service.join(w.u["host"], code)


# ---------- hidden problems ----------

async def test_problems_hidden_from_everyone_but_the_host(client, world):
    w = world

    def login(user):
        sv = users.by_id(user["id"])["session_version"]
        data = base64.b64encode(json.dumps({"uid": user["id"], "sv": sv}).encode())
        client.cookies.set("cf_session", TimestampSigner(str(config.SECRET_KEY)).sign(data).decode())

    code = contest.create(w.u["host"], form())
    service.join(w.u["c0"], code)
    # what the host sees
    login(w.u["host"])
    host_state = client.get(f"/api/matches/{code}").json()
    assert [p["key"] for p in host_state["host"]["problems"]] == list(KEYS)
    assert client.post("/api/problems/validate", json={"text": "2000C", "match_code": code}).status_code == 200
    # a player, a logged-in stranger and an anonymous spectator see nothing of them
    for who in (w.u["c0"], w.u["c1"], None):
        if who:
            login(who)
        else:
            client.cookies.clear()
        state = client.get(f"/api/matches/{code}").json()
        assert state["problems"] == [] and "host" not in state and state["contest"]["problem_count"] == 3
        no_leak(state)
        r = client.post("/api/problems/validate", json={"text": "2000C", "match_code": code})
        assert r.status_code in (401, 403)
        assert client.get(f"/api/matches/{code}/timeline").status_code == 409
        assert client.get(f"/api/matches/{code}/result").status_code == 409
        no_leak(client.get("/api/events").json())
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()                                           # hello
            ws.send_json({"type": "subscribe", "data": {"code": code}})
            msg = ws.receive_json()
            assert msg["type"] == "match.state"
            no_leak(msg["data"])
    # the page itself is static: no data in the HTML
    no_leak(client.get(f"/m/{code}").text)


async def test_host_sees_who_already_tried_a_problem(world):
    w = world
    code = contest.create(w.u["host"], form())
    for h in ("c0", "c1", "c2"):
        service.join(w.u[h], code)
        db.execute("INSERT INTO touched(user_id, problem_key, solved) VALUES (?, '2000C', 0)", (w.u[h]["id"],))
    st = view.build(store.match_by_code(code), w.u["host"]["id"])
    assert st["host"]["warnings"] == ["3 players have already tried 2000C (c0, c1, c2)"]
    assert "host" not in view.build(store.match_by_code(code), w.u["c0"]["id"])


# ---------- scheduled start ----------

async def test_scheduled_auto_start_teams(world):
    w = world
    code = contest.create(w.u["host"], form())
    m = store.match_by_code(code)
    for h in HANDLES:
        service.join(w.u[h], code)
    assert sorted(p["team"] for p in store.players(m["id"])) == [0, 0, 0, 1, 1, 1, 2, 2, 2]
    db.execute("UPDATE users SET history_synced = 0 WHERE handle = 'c4'")
    # 4 minutes before: missing histories are synced and handles refreshed, ahead of time
    w.now.t = T0 + 3600 - 240
    await lifecycle.tick()
    await settle()
    assert db.scalar("SELECT history_synced FROM users WHERE handle = 'c4'") == 1
    assert store.match_by_id(m["id"])["status"] == "lobby"
    calls_before = len(w.cf.calls)
    w.now.t = T0 + 3600
    await lifecycle.tick()
    await settle()
    m = store.match_by_id(m["id"])
    assert m["status"] == "running" and m["start_time"] == T0 + 3600 + config.COUNTDOWN_S
    start_calls = w.cf.calls[calls_before:]
    info = [c for c in start_calls if c[0] == "user.info"]
    assert len(info) == 1 and len(info[0][1]["handles"].split(";")) == 9        # one batched call
    assert not [c for c in start_calls if c[0] == "user.status"]                  # no history sync at the start
    assert set(poller.watches) == {w.u[h]["id"] for h in HANDLES}                # the host is never polled
    assert [p["problem_key"] for p in store.problems(m["id"])] == list(KEYS)
    w.now.t = m["start_time"]
    st = view.build(m, None)
    assert {r["name"] for r in st["standings"]} == {"Owls", "Team B", "Foxes"}
    assert st["freeze_start"] == m["end_time"] - 30 * 60


async def test_auto_start_blocked_by_uneven_teams_then_start_anyway(world):
    w = world
    code = contest.create(w.u["host"], form(team_size=2, scheduled_at=T0 + 60))
    for h in ("c0", "c1", "c2", "c3"):
        service.join(w.u[h], code)                         # 2 / 1 / 1
    w.now.t = T0 + 60
    await lifecycle.tick()
    await settle()
    m = store.match_by_code(code)
    assert m["status"] == "lobby" and "even" in store.settings(m)["start_note"]
    with pytest.raises(service.MatchError, match="host"):
        contest.start_now(w.u["c0"], code)
    assert await contest.start(m["id"], force=True)
    m = store.match_by_id(m["id"])
    assert m["status"] == "running" and store.settings(m)["start_note"] is None


async def test_start_retries_when_codeforces_is_down(world):
    from app.cf.client import CFError
    w = world
    code = contest.create(w.u["host"], form(format="solo", scheduled_at=T0 + 60))
    for h in ("c0", "c1"):
        service.join(w.u[h], code)
    w.cf.on("user.info", lambda p: CFError("down"))
    w.now.t = T0 + 60
    await lifecycle.tick()
    await settle()
    m = store.match_by_code(code)
    assert m["status"] == "lobby" and "not answering" in store.settings(m)["start_note"]
    w.cf.on("user.info", lambda p: [{"handle": h, "rating": 1500} for h in p["handles"].split(";")])
    w.now.t += 10
    await lifecycle.tick()
    await settle()
    assert store.match_by_code(code)["status"] == "lobby"               # waits 30 s between tries
    w.now.t += 30
    await lifecycle.tick()
    await settle()
    assert store.match_by_code(code)["status"] == "running"


async def test_renamed_handle_sits_out_instead_of_blocking(world):
    from app.cf.client import HandleNotFound
    w = world
    code = contest.create(w.u["host"], form(format="solo", scheduled_at=T0 + 60))
    for h in ("c0", "c1", "c2"):
        service.join(w.u[h], code)

    def info(p):
        if "c1" in p["handles"].split(";"):
            return HandleNotFound("c1")
        return [{"handle": h, "rating": 1500} for h in p["handles"].split(";")]
    w.cf.on("user.info", info)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    assert {p["handle"] for p in store.players(m["id"])} == {"c0", "c2"}


# ---------- host controls ----------

async def test_extend_announce_and_end_early(world):
    w = world
    code = contest.create(w.u["host"], form(format="solo", freeze_min=10, scheduled_at=None))
    for h in ("c0", "c1", "c2"):
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    end, fs = m["end_time"], view.freeze_start(m, store.settings(m))
    w.now.t = m["start_time"] + 600
    with pytest.raises(service.MatchError, match="host"):
        contest.extend(w.u["c0"], code, 10)
    with pytest.raises(service.MatchError, match="1–120"):
        contest.extend(w.u["host"], code, 0)
    assert contest.extend(w.u["host"], code, 15) == end + 900
    m = store.match_by_id(m["id"])
    assert all(p["end_time"] == end + 900 for p in store.players(m["id"]))
    assert all(wt.window_end == end + 900 for wt in poller.watches.values())
    assert view.freeze_start(m, store.settings(m)) == fs + 900               # freeze not started yet: moves along
    contest.announce(w.u["host"], code, "  Problem B: n ≤ 10^5, not 10^6.  ")
    with pytest.raises(service.MatchError, match="host"):
        contest.announce(w.u["c0"], code, "hi")
    with pytest.raises(service.MatchError, match="500"):
        contest.announce(w.u["host"], code, "x" * 501)
    for viewer in (w.u["c0"]["id"], None, w.u["host"]["id"]):                 # players, spectators and the host
        bodies = [a["body"] for a in view.build(m, viewer)["announcements"]]
        assert bodies == ["Problem B: n ≤ 10^5, not 10^6.", "Time extended by 15 minutes."]
    # extending inside the freeze keeps the board frozen
    w.now.t = fs + 900 + 60
    contest.extend(w.u["host"], code, 5)
    m = store.match_by_id(m["id"])
    assert view.freeze_start(m, store.settings(m)) == fs + 900
    contest.end_early(w.u["host"], code)
    m = store.match_by_id(m["id"])
    assert m["status"] == "ended" and m["end_reason"] == "host"


async def test_kick_move_and_allow(world):
    w = world
    code = contest.create(w.u["host"], form())
    for h in ("c0", "c1", "c2"):
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    service.move_player(w.u["host"], code, w.u["c0"]["id"], 2)
    assert {p["handle"]: p["team"] for p in store.players(m["id"])}["c0"] == 2
    with pytest.raises(service.MatchError, match="host"):
        service.move_player(w.u["c1"], code, w.u["c0"]["id"], 0)
    contest.kick(w.u["host"], code, w.u["c1"]["id"])
    assert "c1" not in {p["handle"] for p in store.players(m["id"])}
    with pytest.raises(service.MatchError, match="removed you"):
        service.join(w.u["c1"], code)
    assert [k["handle"] for k in view.build(m, w.u["host"]["id"])["host"]["kicked"]] == ["c1"]
    contest.allow(w.u["host"], code, w.u["c1"]["id"])
    service.join(w.u["c1"], code)
    # per-team invite link straight into Foxes
    service.join(w.u["c3"], code, 2)
    assert {p["handle"]: p["team"] for p in store.players(m["id"])}["c3"] == 2


async def test_solo_contest_scoring_without_host(world):
    w = world
    code = contest.create(w.u["host"], form(format="solo", max_players=12, freeze_min=0, scheduled_at=None))
    for h in HANDLES:
        service.join(w.u[h], code)
    m = store.match_by_code(code)
    assert await contest.start(m["id"])
    m = store.match_by_id(m["id"])
    start = m["start_time"]
    w.subs["c3"] += [raw(1, "2000C", "OK", start + 120)]
    w.now.t = start + 200
    await poller.check(w.u["c3"]["id"])
    st = view.build(m, None)
    assert len(st["standings"]) == 9 and st["standings"][0]["handle"] == "c3"
    assert w.u["host"]["id"] not in {r["user_id"] for r in st["standings"]}


# ---------- Events listing ----------

async def test_events_listing_and_visibility(client, world):
    w = world
    pub = contest.create(w.u["host"], form(title="Public one"))
    hidden = contest.create(w.u["host"], form(title="Secret one", visibility="unlisted"))
    service.join(w.u["c0"], hidden)
    live = contest.create(w.u["c8"], form(title="Live one", format="solo", scheduled_at=None))
    for h in ("c1", "c2"):
        service.join(w.u[h], live)
    assert await contest.start(store.match_by_code(live)["id"])
    anon = contest.listing(None)
    assert [e["title"] for e in anon["upcoming"]] == ["Public one"]
    assert [e["title"] for e in anon["live"]] == ["Live one"] and anon["mine"] == []
    item = anon["upcoming"][0]
    assert item["host"]["handle"] == "host" and item["slots"] == 9 and item["problem_count"] == 3
    assert item["team_count"] == 3 and item["team_size"] == 3 and item["duration_min"] == 120
    host = contest.listing(w.u["host"]["id"])
    assert {e["title"] for e in host["mine"]} == {"Public one", "Secret one"}
    assert all(e["role"] == "host" for e in host["mine"])
    player = contest.listing(w.u["c0"]["id"])
    assert [(e["title"], e["role"]) for e in player["mine"]] == [("Secret one", "player")]
    # past: the winner is listed
    m = store.match_by_code(live)
    w.subs["c2"] += [raw(1, "2000C", "OK", m["start_time"] + 60)]
    w.now.t = m["start_time"] + 100
    await poller.check(w.u["c2"]["id"])
    contest.end_early(w.u["c8"], live)
    w.now.t += 60
    for _ in range(5):
        await lifecycle.tick()
        await settle()
    past = contest.listing(None)["past"]
    assert past[0]["title"] == "Live one" and past[0]["winner"]["players"][0]["handle"] == "c2"
    assert client.get("/api/events").status_code == 200
    assert pub and hidden


async def test_contest_lobby_is_not_cancelled_as_idle(world):
    w = world
    code = contest.create(w.u["host"], form(scheduled_at=T0 + 5 * 86400))
    w.now.t = T0 + 3 * 86400
    await lifecycle.tick()
    assert store.match_by_code(code)["status"] == "lobby"
    w.now.t = T0 + 5 * 86400 + config.CONTEST_STALE_S + 1     # long past its start with nobody there
    await lifecycle.tick()
    assert store.match_by_code(code)["status"] == "cancelled"


# ---------- migration ----------

def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE matches (id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, mode TEXT NOT NULL, rules TEXT NOT NULL,
          status TEXT NOT NULL, result_status TEXT NOT NULL DEFAULT 'none', settings TEXT NOT NULL,
          creator_id INTEGER, rematch_of INTEGER, created_at INTEGER NOT NULL, start_time INTEGER, end_time INTEGER,
          ended_at INTEGER, end_reason TEXT, result TEXT);
        CREATE TABLE match_players (match_id INTEGER NOT NULL, user_id INTEGER NOT NULL, handle TEXT NOT NULL,
          cf_rating INTEGER, joined_at INTEGER NOT NULL, ready INTEGER NOT NULL DEFAULT 0, end_time INTEGER,
          penalty_bonus INTEGER NOT NULL DEFAULT 0, rating_min INTEGER, rating_max INTEGER, forfeited_at INTEGER,
          final_checked INTEGER NOT NULL DEFAULT 0, place INTEGER, solved INTEGER, penalty INTEGER, points REAL,
          rating_before INTEGER, rating_after INTEGER, PRIMARY KEY (match_id, user_id));
        INSERT INTO matches(id, code, mode, rules, status, settings, created_at) VALUES (1, 'OLDTMS', 'team', 'icpc',
          'ended', '{"team_size": 2}', 0);
    """)
    old.close()
    conn = db.connect(str(path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
        assert {"scheduled_at", "visibility"} <= cols
        assert "team" in {r[1] for r in conn.execute("PRAGMA table_info(match_players)")}
        assert [r["name"] for r in conn.execute("SELECT name FROM match_teams WHERE match_id = 1 ORDER BY idx")] == \
            ["Team A", "Team B"]
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    finally:
        conn.close()
        db._conn = None
