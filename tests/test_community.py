"""The Players page: members list, activity feed, saved challenges."""

import pytest

from app import community, db
from app.auth import users
from app.matches import service, store
from app.ws.hub import hub
from tests.test_match_flow import T0, finish, raw, start_duel, world  # noqa: F401  (fixture)


@pytest.fixture
def carol(world):  # noqa: F811
    return users.by_id(users.create("carol", 1900, 2000))


def lobby(w):
    return service.create(w.a, "friend", "icpc", {"count": 3, "rating_min": 1400, "rating_max": 1800, "duration_min": 60})


def test_challenge_reaches_someone_offline(world, carol):  # noqa: F811
    w = world
    code = lobby(w)
    sent = []
    hub_send = hub.send_user
    hub.send_user = lambda uid, t, d: sent.append((uid, t))
    try:
        r = community.send(w.a, code, "CAROL")
    finally:
        hub.send_user = hub_send
    assert r == {"ok": True, "online": False, "handle": "carol"}
    assert (carol["id"], "invite") in sent

    assert community.incoming_count(carol["id"]) == 1
    inc = community.lists(carol["id"])["incoming"]
    assert inc[0]["from"]["handle"] == "alice" and inc[0]["code"] == code
    assert inc[0]["settings"] == {"rules": "icpc", "duration_min": 60, "count": 3, "custom": False,
                                  "rating_min": 1400, "rating_max": 1800}
    assert community.lists(w.a["id"])["outgoing"][0]["status"] == "waiting"

    service.join(carol, code)  # accepting = joining the lobby
    assert community.incoming_count(carol["id"]) == 0
    assert community.lists(w.a["id"])["outgoing"][0]["status"] == "joined"


def test_decline_and_full_or_closed_lobbies_drop_out(world, carol):  # noqa: F811
    w = world
    code = lobby(w)
    community.send(w.a, code, "carol")
    community.send(w.a, code, "Bob_77")
    cid = community.lists(carol["id"])["incoming"][0]["id"]
    community.decline(carol, cid)
    assert community.incoming_count(carol["id"]) == 0
    statuses = {c["to"]["handle"]: c["status"] for c in community.lists(w.a["id"])["outgoing"]}
    assert statuses == {"carol": "declined", "Bob_77": "waiting"}
    with pytest.raises(community.ChallengeError):
        community.decline(w.b, cid)  # not yours

    # Bob takes the seat: the lobby is full, so a fresh challenge to carol isn't shown
    community.send(w.a, code, "carol")
    assert community.incoming_count(carol["id"]) == 1
    service.join(w.b, code)
    assert community.incoming_count(carol["id"]) == 0

    # a cancelled lobby takes its challenges with it
    service.leave(w.b, code)
    service.leave(w.a, code)
    assert community.incoming_count(carol["id"]) == 0
    assert community.lists(w.a["id"])["outgoing"] == []


def test_challenge_rules(world, carol, monkeypatch):  # noqa: F811
    w = world
    code = lobby(w)
    with pytest.raises(community.ChallengeError, match="That's you"):
        community.send(w.a, code, "alice")
    with pytest.raises(community.ChallengeError, match="No player"):
        community.send(w.a, code, "nobody")
    with pytest.raises(community.ChallengeError, match="Only players"):
        community.send(w.b, code, "carol")
    db.execute("UPDATE users SET banned_at = 1 WHERE id = ?", (carol["id"],))
    with pytest.raises(community.ChallengeError, match="No player"):
        community.send(w.a, code, "carol")
    db.execute("UPDATE users SET banned_at = NULL")
    monkeypatch.setattr(community, "CHALLENGES_PER_HOUR", 1)
    community.send(w.a, code, "carol")
    with pytest.raises(community.ChallengeError, match="a lot of challenges"):
        community.send(w.a, code, "Bob_77")


async def test_activity_and_players(world, carol):  # noqa: F811
    w = world
    match = await start_duel(w)
    feed = community.activity()
    assert [m["code"] for m in feed["live"]] == [match["code"]]
    people = {p["handle"]: p for p in community.players(carol["id"])}
    assert people["alice"]["playing"] == {"code": match["code"], "mode": "friend"}
    assert people["carol"]["is_me"] and not people["carol"]["playing"]
    assert community.players(carol["id"])[0]["handle"] == "carol"  # you first, then whoever is around

    key = store.problems(match["id"])[0]["problem_key"]
    w.subs["Bob_77"] += [raw(1, key, "OK", match["start_time"] + 600)]
    w.now.t = match["end_time"] + 30
    await finish(w, match)
    feed = community.activity()
    assert feed["live"] == []
    m = next(i for i in feed["items"] if i["kind"] == "match")
    assert m["code"] == match["code"] and not m["draw"]
    assert [s["players"][0]["handle"] for s in m["sides"]] == ["Bob_77", "alice"]  # winner first
    assert {i["handle"] for i in feed["items"] if i["kind"] == "joined"} == {"alice", "Bob_77", "carol"}
    assert {p["handle"]: p["played"] for p in community.players(None)} == {"alice": 1, "Bob_77": 1, "carol": 0}

    db.execute("UPDATE users SET banned_at = 1 WHERE id = ?", (carol["id"],))
    assert "carol" not in {p["handle"] for p in community.players(None)}


def test_unlisted_contests_stay_off_the_feed(world):  # noqa: F811
    w = world
    db.execute("INSERT INTO matches(code, mode, rules, status, settings, creator_id, created_at, start_time, end_time,"
               " visibility) VALUES ('HIDDEN', 'contest', 'icpc', 'running', '{}', ?, ?, ?, ?, 'unlisted')",
               (w.a["id"], T0, T0, T0 + 3600))
    mid = db.scalar("SELECT id FROM matches WHERE code = 'HIDDEN'")
    db.execute("INSERT INTO match_players(match_id, user_id, handle, joined_at) VALUES (?,?,?,?)",
               (mid, w.a["id"], "alice", T0))
    assert community.activity()["live"] == []
    assert next(p for p in community.players(None) if p["handle"] == "alice")["playing"] is None


def test_schema_has_challenges(world):  # noqa: F811
    assert db.SCHEMA_VERSION >= 5
    assert db.one("SELECT name FROM sqlite_master WHERE name = 'challenges'") is not None
