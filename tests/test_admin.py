"""Admin page: owner-only access, the numbers and tables, cancel, ban/unban, the v3 migration."""

import base64
import json
import sqlite3

import pytest
from itsdangerous import TimestampSigner

from app import admin, config, db
from app.auth import users
from app.matches import locks, queue, service, store
from app.poller.poller import poller
from app.ws.hub import hub
from tests.test_api_auth import fill

T0 = 1_750_000_000

PAGE_ROUTES = ["/admin"]
GET_ROUTES = ["/api/admin/stats", "/api/admin/users", "/api/admin/users?q=a&sort=matches&page=2",
              "/api/admin/matches", "/api/admin/matches?state=running&mode=team", "/api/admin/activity"]
POST_ROUTES = ["/api/admin/matches/AAAAAA/cancel", "/api/admin/users/2/ban", "/api/admin/users/2/unban"]


def login(client, user):
    sv = users.by_id(user["id"])["session_version"]
    data = base64.b64encode(json.dumps({"uid": user["id"], "sv": sv}).encode())
    client.cookies.set("cf_session", TimestampSigner(str(config.SECRET_KEY)).sign(data).decode())


@pytest.fixture
def site(client, frozen_now, monkeypatch):
    """The owner, a handful of players and some matches."""
    monkeypatch.setattr(config, "ADMIN_HANDLES", config.parse_admin_handles(None))
    frozen_now.t = T0
    owner = users.create("GandalfTheGay", 1900, 2000)
    ids = {"owner": owner}
    for i, h in enumerate(["alice", "bob", "carol", "dave"]):
        frozen_now.t = T0 + 100 * (i + 1)
        ids[h] = users.create(h, 1200 + 100 * i, 1900)
    frozen_now.t = T0 + 10_000
    db.execute("UPDATE users SET last_seen_at = ? WHERE handle IN ('alice', 'bob')", (frozen_now.t - 60,))
    db.execute("UPDATE users SET last_seen_at = ? WHERE handle = 'carol'", (frozen_now.t - 3600,))
    # a finished duel alice beat bob in, and a running one between carol and dave
    db.execute("INSERT INTO matches(id, code, mode, rules, status, result_status, settings, creator_id, created_at,"
               " start_time, end_time, ended_at) VALUES (1, 'DONE01', 'friend', 'icpc', 'ended', 'final', '{}', ?, ?, ?, ?, ?)",
               (ids["alice"], T0 + 1000, T0 + 1005, T0 + 4605, T0 + 4605))
    db.executemany("INSERT INTO match_players(match_id, user_id, handle, joined_at, place) VALUES (1, ?, ?, ?, ?)",
                   [(ids["alice"], "alice", T0, 1), (ids["bob"], "bob", T0, 2)])
    db.execute("INSERT INTO matches(id, code, mode, rules, status, settings, creator_id, created_at, start_time, end_time)"
               " VALUES (2, 'LIVE02', 'friend', 'lockout', 'running', '{}', ?, ?, ?, ?)",
               (ids["carol"], frozen_now.t - 600, frozen_now.t - 590, frozen_now.t + 3000))
    for h in ("carol", "dave"):
        db.execute("INSERT INTO match_players(match_id, user_id, handle, joined_at, end_time) VALUES (2, ?, ?, ?, ?)",
                   (ids[h], h, T0, frozen_now.t + 3000))
        locks.acquire(ids[h], "match", 2)
        poller.watch(ids[h], h, frozen_now.t - 590, frozen_now.t + 3000)
    db.execute("INSERT INTO matches(id, code, mode, rules, status, settings, creator_id, created_at)"
               " VALUES (3, 'CONT03', 'contest', 'icpc', 'lobby', '{\"title\": \"Friday Clash\"}', ?, ?)",
               (ids["owner"], frozen_now.t - 50))

    class S:
        pass
    s = S()
    s.client, s.now, s.ids = client, frozen_now, ids
    s.u = lambda h: users.by_id(ids[h])
    yield s
    poller.watches.clear()


# ---------- who is an admin ----------

def test_admin_handles_parsing():
    assert config.parse_admin_handles(None) == {"gandalfthegay"}
    assert config.parse_admin_handles(" Tourist , gandalfTheGay,,") == {"tourist", "gandalfthegay"}
    assert config.parse_admin_handles("") == frozenset()


def test_everyone_else_gets_404(site):
    c = site.client
    for who in (None, "alice"):
        c.cookies.clear()
        if who:
            login(c, site.u(who))
        for path in PAGE_ROUTES + GET_ROUTES:
            r = c.get(path)
            assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, (who, path)
        for path in POST_ROUTES:
            r = c.post(path)
            assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, (who, path)
        me = c.get("/api/me").json()
        assert not me.get("is_admin")
    # nothing changed
    assert store.match_by_code("LIVE02")["status"] == "running"


def test_admin_match_is_case_insensitive_and_configurable(site, monkeypatch):
    c = site.client
    login(c, site.u("owner"))
    assert c.get("/admin").status_code == 200
    assert c.get("/api/me").json()["is_admin"] is True
    monkeypatch.setattr(config, "ADMIN_HANDLES", config.parse_admin_handles("ALICE"))
    assert c.get("/admin").status_code == 404
    login(c, site.u("alice"))
    assert c.get("/admin").status_code == 200 and "page-admin.js" in c.get("/admin").text
    monkeypatch.setattr(config, "ADMIN_HANDLES", config.parse_admin_handles(""))
    assert c.get("/api/admin/stats").status_code == 404


def test_posts_still_need_the_csrf_header(site):
    c = site.client
    login(c, site.u("owner"))
    r = c.post("/api/admin/users/2/ban", headers={"X-Requested-With": ""})
    assert r.status_code == 403
    assert site.u("alice")["banned_at"] is None


# ---------- what the admin sees ----------

def test_stats(site):
    c = site.client
    login(c, site.u("owner"))
    s = c.get("/api/admin/stats").json()
    assert s["users"] == {"total": 5, "new_24h": 5, "new_7d": 5, "active_now": 2, "banned": 0}
    assert s["matches"]["by_state"] == {"lobby": 1, "starting": 0, "running": 1, "ended": 1, "cancelled": 0}
    assert s["matches"]["last_24h"] == 3 and s["matches"]["contests_upcoming"] == 1
    assert s["cf"]["status"] == "ok" and "sockets" in s["live"]
    site.now.t += 2 * 86400
    s = c.get("/api/admin/stats").json()
    assert s["users"]["new_24h"] == 0 and s["users"]["new_7d"] == 5 and s["users"]["active_now"] == 0


def test_users_table_search_sort_and_pages(site, monkeypatch):
    c = site.client
    login(c, site.u("owner"))
    d = c.get("/api/admin/users").json()
    assert d["total"] == 5 and [u["handle"] for u in d["users"]] == ["dave", "carol", "bob", "alice", "GandalfTheGay"]
    alice = next(u for u in d["users"] if u["handle"] == "alice")
    assert alice["played"] == 1 and alice["wins"] == 1 and alice["cf_rating"] == 1200
    assert next(u for u in d["users"] if u["handle"] == "GandalfTheGay")["is_admin"]

    by = lambda sort: [u["handle"] for u in c.get(f"/api/admin/users?sort={sort}").json()["users"]]  # noqa: E731
    assert by("seen")[:3] == ["bob", "alice", "carol"]  # same time: newest account first
    assert by("seen")[-2:] == ["dave", "GandalfTheGay"]  # never seen: last
    assert by("matches")[:2] == ["bob", "alice"]
    assert by("rating")[0] == "GandalfTheGay" and by("rating")[1] == "dave"

    assert [u["handle"] for u in c.get("/api/admin/users?q=LIC").json()["users"]] == ["alice"]
    assert c.get("/api/admin/users?q=%25").json()["total"] == 0  # LIKE wildcards are literal
    assert c.get("/api/admin/users?q=_").json()["total"] == 0

    monkeypatch.setattr(config, "ADMIN_PAGE_SIZE", 2)
    pages = [c.get(f"/api/admin/users?page={p}").json() for p in (1, 2, 3, 4)]
    assert [len(p["users"]) for p in pages] == [2, 2, 1, 0]
    seen = [u["handle"] for p in pages for u in p["users"]]
    assert len(set(seen)) == 5
    assert c.get("/api/admin/users?page=-3&sort=nonsense").json()["page"] == 1


def test_matches_table_filters_and_pages(site, monkeypatch):
    c = site.client
    login(c, site.u("owner"))
    d = c.get("/api/admin/matches").json()
    assert [m["code"] for m in d["matches"]] == ["CONT03", "LIVE02", "DONE01"]
    live = d["matches"][1]
    assert live["players"] == 2 and live["creator"] == "carol" and live["rules"] == "lockout"
    assert d["matches"][0]["title"] == "Friday Clash" and d["matches"][0]["players"] == 0
    assert [m["code"] for m in c.get("/api/admin/matches?state=running").json()["matches"]] == ["LIVE02"]
    assert [m["code"] for m in c.get("/api/admin/matches?mode=contest").json()["matches"]] == ["CONT03"]
    assert c.get("/api/admin/matches?mode=friend&state=lobby").json()["total"] == 0
    assert c.get("/api/admin/matches?state=bogus").json()["total"] == 3  # unknown filters are ignored
    monkeypatch.setattr(config, "ADMIN_PAGE_SIZE", 2)
    p2 = c.get("/api/admin/matches?page=2").json()
    assert p2["total"] == 3 and [m["code"] for m in p2["matches"]] == ["DONE01"]


def test_activity_feed(site):
    c = site.client
    login(c, site.u("owner"))
    items = c.get("/api/admin/activity").json()["items"]
    kinds = {(i["kind"], i.get("code") or i.get("handle")) for i in items}
    assert {("created", "CONT03"), ("finished", "DONE01"), ("signup", "dave")} <= kinds
    assert [i["at"] for i in items] == sorted((i["at"] for i in items), reverse=True)


# ---------- cancel ----------

def test_cancel_running_match_notifies_players(site, monkeypatch):
    c = site.client
    sent = []
    monkeypatch.setattr(hub, "send_match", lambda code, type_, data: sent.append((code, type_)))
    login(c, site.u("owner"))
    assert c.post("/api/admin/matches/live02/cancel").json() == {"ok": True}
    m = store.match_by_code("LIVE02")
    assert m["status"] == "cancelled" and m["end_reason"] == "cancelled" and m["ended_at"] == site.now.t
    assert locks.current(site.ids["carol"]) is None and locks.current(site.ids["dave"]) is None
    assert site.ids["carol"] not in poller.watches
    assert ("LIVE02", "match.ended") in sent and ("LIVE02", "match.state") in sent
    # only lobbies and running matches
    r = c.post("/api/admin/matches/DONE01/cancel")
    assert r.status_code == 400 and store.match_by_code("DONE01")["status"] == "ended"
    assert c.post("/api/admin/matches/NOPE99/cancel").status_code == 400


def test_cancel_lobby(site, monkeypatch):
    c = site.client
    sent = []
    monkeypatch.setattr(hub, "send_match", lambda code, type_, data: sent.append((code, type_)))
    code = service.create(site.u("alice"), "friend", "icpc", {})
    login(c, site.u("owner"))
    assert c.post(f"/api/admin/matches/{code}/cancel").status_code == 200
    assert store.match_by_code(code)["status"] == "cancelled"
    assert locks.current(site.ids["alice"]) is None
    assert (code, "match.state") in sent and (code, "match.ended") not in sent


# ---------- ban ----------

def test_ban_blocks_everything_and_unban_restores(site, stub_cf):
    c = site.client
    fill()
    stub_cf.on("user.info", lambda p: [{"handle": "bob", "rating": 1300, "maxRating": 1900}])
    lobby = service.create(site.u("bob"), "friend", "icpc", {})  # bob waits in a lobby
    other = service.create(site.u("alice"), "friend", "icpc", {})

    # bob has a session open
    login(c, site.u("bob"))
    bob_cookie = c.cookies.get("cf_session")
    assert c.get("/api/me").json()["user"]["handle"] == "bob"

    c.cookies.clear()
    login(c, site.u("owner"))
    assert c.post(f"/api/admin/users/{site.ids['bob']}/ban").json() == {"ok": True}
    assert site.u("bob")["banned_at"] == site.now.t
    assert store.match_by_code(lobby)["status"] == "cancelled" and locks.current(site.ids["bob"]) is None
    assert c.get("/api/admin/stats").json()["users"]["banned"] == 1

    # the old session is dead
    c.cookies.clear()
    c.cookies.set("cf_session", bob_cookie)
    assert c.get("/api/me").json() == {"user": None}
    assert c.post(f"/api/matches/{other}/join").status_code == 401
    assert c.post("/api/matches", json={"mode": "friend", "rules": "icpc", "settings": {}}).status_code == 401
    # and the service layer refuses too
    with pytest.raises(service.MatchError):
        service.join(site.u("bob"), other)
    with pytest.raises(service.MatchError):
        service.create(site.u("bob"), "friend", "icpc", {})
    with pytest.raises(queue.QueueError):
        queue.join(site.u("bob"))

    # can't log in again
    c.cookies.clear()
    r = c.post("/api/auth/start", json={"handle": "Bob"})
    assert r.status_code == 400 and "banned" in r.json()["detail"]

    # unban: logging in works again (with a fresh session)
    login(c, site.u("owner"))
    assert c.post(f"/api/admin/users/{site.ids['bob']}/unban").json() == {"ok": True}
    assert site.u("bob")["banned_at"] is None
    c.cookies.clear()
    task = c.post("/api/auth/start", json={"handle": "bob"}).json()
    key = task["problem"]["key"]
    stub_cf.on("user.status", lambda p: [{"id": 5, "creationTimeSeconds": task["issued_at"] + 20,
                                         "verdict": "COMPILATION_ERROR",
                                         "problem": {"contestId": int(key[:-1]), "index": key[-1]}}])
    site.now.t += 30
    assert c.post("/api/auth/check", json={"task_id": task["task_id"]}).json()["status"] == "verified"
    assert c.get("/api/me").json()["user"]["handle"] == "bob"
    assert c.post(f"/api/matches/{other}/join").status_code == 200


def test_ban_mid_verification_refuses_the_login(site, stub_cf):
    c = site.client
    fill()
    stub_cf.on("user.info", lambda p: [{"handle": "carol", "rating": 1400, "maxRating": 1900}])
    task = c.post("/api/auth/start", json={"handle": "carol"}).json()
    admin.ban(site.u("owner"), site.ids["carol"])
    key = task["problem"]["key"]
    stub_cf.on("user.status", lambda p: [{"id": 6, "creationTimeSeconds": task["issued_at"] + 20,
                                         "verdict": "COMPILATION_ERROR",
                                         "problem": {"contestId": int(key[:-1]), "index": key[-1]}}])
    site.now.t += 30
    r = c.post("/api/auth/check", json={"task_id": task["task_id"]})
    assert r.status_code == 403
    assert c.get("/api/me").json() == {"user": None}


def test_cannot_ban_yourself(site):
    c = site.client
    login(c, site.u("owner"))
    r = c.post(f"/api/admin/users/{site.ids['owner']}/ban")
    assert r.status_code == 400 and "yourself" in r.json()["detail"]
    assert site.u("owner")["banned_at"] is None
    assert c.post("/api/admin/users/9999/ban").status_code == 400


def test_banned_user_leaves_the_queue(site):
    queue.join(site.u("alice"))
    admin.ban(site.u("owner"), site.ids["alice"])
    assert queue.status(site.ids["alice"])["in_queue"] is False
    assert locks.current(site.ids["alice"]) is None


# ---------- migration ----------

def test_v2_database_gets_banned_at(tmp_path):
    path = tmp_path / "v2.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, handle TEXT UNIQUE COLLATE NOCASE, cf_rating INTEGER NOT NULL DEFAULT 0,
          cf_max_rating INTEGER NOT NULL DEFAULT 0, duel_rating INTEGER NOT NULL, needs_reverify INTEGER NOT NULL DEFAULT 0,
          session_version INTEGER NOT NULL DEFAULT 1, history_synced INTEGER NOT NULL DEFAULT 0,
          last_sub_id INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, last_seen_at INTEGER);
        INSERT INTO users(id, handle, duel_rating, created_at) VALUES (1, 'old', 1200, 5);
        PRAGMA user_version = 2;
    """)
    old.close()
    conn = db.connect(str(path))
    try:
        assert "banned_at" in {r[1] for r in conn.execute("PRAGMA table_info(users)")}
        assert conn.execute("SELECT handle, banned_at FROM users").fetchone()[:] == ("old", None)
        assert "matches_created" in {r[1] for r in conn.execute("PRAGMA index_list(matches)")}
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 3
    finally:
        conn.close()
        db._conn = None
    # running it again is a no-op
    conn = db.connect(str(path))
    conn.close()
    db._conn = None
