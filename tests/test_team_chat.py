"""Team chat: private per team, rate limited, read-only after the end, migration on an old database."""

import base64
import json
import sqlite3

import pytest
from itsdangerous import TimestampSigner

from app import config, db
from app.matches import chat, contest, service, store, view
from app.ws.hub import hub
from tests.test_contest import HANDLES, T0, form, world  # noqa: F401  (world is a fixture)


@pytest.fixture(autouse=True)
def _fresh_limits():
    chat._sent.clear()
    yield
    chat._sent.clear()
    hub.by_match.clear()


def cookie(user) -> str:
    sv = db.scalar("SELECT session_version FROM users WHERE id = ?", (user["id"],))
    data = base64.b64encode(json.dumps({"uid": user["id"], "sv": sv}).encode())
    return "cf_session=" + TimestampSigner(str(config.SECRET_KEY)).sign(data).decode()


def team_contest(w):
    """Owls (c0 c3 c6), Team B (c1 c4 c7), Foxes (c2 c5 c8)."""
    code = contest.create(w.u["host"], form(scheduled_at=None))
    for h in HANDLES:
        service.join(w.u[h], code)
    return store.match_by_code(code)


class Spy:
    """A fake socket in the hub that records what it receives."""

    def __init__(self, uid, screen=False):
        self.user_id, self.screen, self.matches, self.got = uid, screen, set(), []

    @property
    def viewer(self):
        return None if self.screen else self.user_id


@pytest.fixture
def spies(monkeypatch):
    monkeypatch.setattr(hub, "_spawn", lambda conn, msg: conn.got.append(msg))
    return Spy


async def test_messages_reach_only_the_team(world, spies):
    w = world
    m = team_contest(w)
    owl, owl2, bee, host, anon = (spies(w.u["c0"]["id"]), spies(w.u["c3"]["id"]), spies(w.u["c1"]["id"]),
                                  spies(w.u["host"]["id"]), spies(None))
    screen = spies(w.u["c3"]["id"], screen=True)        # a teammate logged in on the projector laptop
    for s in (owl, owl2, bee, host, anon, screen):
        hub.subscribe(s, m["code"])
    msg = chat.post(m, w.u["c0"], "  push B, I'll take C  ")
    assert msg["body"] == "push B, I'll take C" and msg["team"] == 0
    got = lambda s: [x["data"]["body"] for x in s.got if x["type"] == "chat.message"]  # noqa: E731
    assert got(owl) == got(owl2) == ["push B, I'll take C"]
    assert got(bee) == got(host) == got(anon) == got(screen) == []
    # history: own team only, oldest first
    w.now.t += 5
    chat.post(m, w.u["c1"], "bees only")
    assert [x["body"] for x in chat.history(m, w.u["c3"]["id"])["messages"]] == ["push B, I'll take C"]
    assert [x["body"] for x in chat.history(m, w.u["c4"]["id"])["messages"]] == ["bees only"]
    for outsider in (w.u["host"]["id"], None):
        with pytest.raises(service.MatchError, match="only for players"):
            chat.history(m, outsider)
    with pytest.raises(service.MatchError, match="only for players"):
        chat.post(m, w.u["host"], "hi")
    # nothing of it in the match state anyone gets
    for viewer in (None, w.u["host"]["id"], w.u["c1"]["id"], w.u["c0"]["id"]):
        text = json.dumps(view.build(m, viewer))
        assert "push B" not in text and "bees only" not in text


async def test_length_and_rate_limits(world):
    w = world
    m = team_contest(w)
    u = w.u["c0"]
    with pytest.raises(service.MatchError, match="Write something"):
        chat.post(m, u, " \n\t ")
    with pytest.raises(service.MatchError, match="500"):
        chat.post(m, u, "x" * 501)
    assert chat.post(m, u, "x" * 500)["body"] == "x" * 500
    with pytest.raises(service.MatchError, match="one message per second"):
        chat.post(m, u, "again")                                    # same second
    for i in range(19):
        w.now.t += 1
        chat.post(m, u, f"m{i}")                                    # 20 in the last minute now
    w.now.t += 1
    with pytest.raises(service.MatchError, match="20 messages a minute"):
        chat.post(m, u, "one too many")
    chat.post(m, w.u["c3"], "teammates have their own limit")
    w.now.t += 60
    assert chat.post(m, u, "fine again")["body"] == "fine again"
    # control characters go, line breaks stay
    w.now.t += 1
    assert chat.post(m, u, "a\x07b\nc")["body"] == "ab\nc"


async def test_history_keeps_the_last_200(world):
    w = world
    m = team_contest(w)
    for i in range(205):
        db.execute("INSERT INTO team_messages(match_id, team_idx, user_id, body, created_at) VALUES (?,?,?,?,?)",
                   (m["id"], 0, w.u["c0"]["id"], f"m{i}", T0 + i))
    msgs = chat.history(m, w.u["c0"]["id"])["messages"]
    assert len(msgs) == 200 and msgs[0]["body"] == "m5" and msgs[-1]["body"] == "m204"


async def test_moved_player_sees_only_the_new_team(world):
    w = world
    m = team_contest(w)
    chat.post(m, w.u["c0"], "owls plan")
    chat.post(m, w.u["c2"], "foxes plan")
    contest.kick(w.u["host"], m["code"], w.u["c8"]["id"])            # make room on Foxes
    service.move_player(w.u["host"], m["code"], w.u["c0"]["id"], 2)
    h = chat.history(m, w.u["c0"]["id"])
    assert h["team"] == 2 and [x["body"] for x in h["messages"]] == ["foxes plan"]
    assert [x["body"] for x in chat.history(m, w.u["c3"]["id"])["messages"]] == ["owls plan"]


async def test_read_only_after_the_end(world):
    w = world
    m = team_contest(w)
    chat.post(m, w.u["c0"], "gl")
    assert await contest.start(m["id"])
    w.now.t += 10
    chat.post(m, w.u["c0"], "running")
    contest.end_early(w.u["host"], m["code"])
    m = store.match_by_id(m["id"])
    with pytest.raises(service.MatchError, match="closed"):
        w.now.t += 10
        chat.post(m, w.u["c0"], "gg")
    h = chat.history(m, w.u["c3"]["id"])
    assert not h["open"] and [x["body"] for x in h["messages"]] == ["gl", "running"]


async def test_no_chat_outside_team_matches(world):
    w = world
    code = contest.create(w.u["host"], form(format="solo", scheduled_at=None))
    service.join(w.u["c0"], code)
    m = store.match_by_code(code)
    with pytest.raises(service.MatchError, match="only for players"):
        chat.post(m, w.u["c0"], "hello")


def test_over_the_websocket(client, world):
    w = world
    m = team_contest(w)
    code = m["code"]
    client.cookies.clear()
    with client.websocket_connect("/ws", headers={"cookie": cookie(w.u["c0"])}) as owl, \
            client.websocket_connect("/ws", headers={"cookie": cookie(w.u["c1"])}) as bee, \
            client.websocket_connect("/ws", headers={"cookie": cookie(w.u["host"])}) as host, \
            client.websocket_connect("/ws?view=screen", headers={"cookie": cookie(w.u["c3"])}) as screen:
        for ws in (owl, bee, host, screen):
            ws.receive_json()                                          # hello
            ws.send_json({"type": "subscribe", "data": {"code": code}})
            assert ws.receive_json()["type"] == "match.state"
        for ws in (owl, bee):                                          # players get their team's history on join
            first = ws.receive_json()
            assert first["type"] == "chat.history" and first["data"]["messages"] == []
        owl.send_json({"type": "chat.send", "data": {"code": code, "body": "<b>hi</b> team"}})
        got = owl.receive_json()
        assert got["type"] == "chat.message" and got["data"]["body"] == "<b>hi</b> team"  # stored as plain text
        # the host asking for a team's chat gets an error, not the messages
        host.send_json({"type": "chat.history", "data": {"code": code}})
        assert host.receive_json()["type"] == "chat.error"
        screen.send_json({"type": "chat.history", "data": {"code": code}})
        screen.send_json({"type": "ping"})
        assert screen.receive_json()["type"] == "pong"                # the screen socket gets no chat at all
        bee.send_json({"type": "chat.history", "data": {"code": code}})
        h = bee.receive_json()
        assert h["type"] == "chat.history" and h["data"]["team"] == 1 and h["data"]["messages"] == []
        bee.send_json({"type": "chat.send", "data": {"code": code, "body": "y" * 501}})
        err = bee.receive_json()
        assert err["type"] == "chat.error" and "500" in err["data"]["message"]
    # and none of it over the REST API
    client.headers["cookie"] = cookie(w.u["c1"])
    assert "hi</b>" not in client.get(f"/api/matches/{code}").text


# ---------- migration ----------

def test_migration_adds_team_messages_to_an_old_database(tmp_path):
    path = str(tmp_path / "old.db")
    db.connect(path)
    db.conn().execute("DROP TABLE team_messages")
    db.conn().execute("PRAGMA user_version=2")
    db.conn().close()
    db._conn = None
    raw = sqlite3.connect(path)
    assert "team_messages" not in {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    raw.close()
    for _ in range(2):                                                  # idempotent: safe to run again
        c = db.connect(path)
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master")}
        assert {"team_messages", "team_messages_match"} <= names
        assert c.execute("PRAGMA user_version").fetchone()[0] >= 3
        cols = {r[1] for r in c.execute("PRAGMA table_info(team_messages)")}
        assert cols == {"id", "match_id", "team_idx", "user_id", "body", "created_at"}
    c.close()
    db._conn = None
