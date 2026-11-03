from app.cf import cache
from app.cf.parse import parse_contests, parse_problems
from tests.conftest import load


def fill():
    cache.store_contests(parse_contests(load("contest_list.json")["result"]))
    cache.store_problems(parse_problems(load("problemset.json")["result"]))


def test_csrf_header_required(client):
    r = client.post("/api/auth/start", json={"handle": "alice"}, headers={"X-Requested-With": ""})
    assert r.status_code == 403


def test_login_flow_sets_cookie(client, stub_cf, frozen_now):
    fill()
    stub_cf.on("user.info", lambda p: [{"handle": "alice", "rating": 1650, "maxRating": 1700}])
    assert client.get("/api/me").json() == {"user": None}
    task = client.post("/api/auth/start", json={"handle": "Alice"}).json()
    key = task["problem"]["key"]
    stub_cf.on("user.status", lambda p: [{"id": 5, "creationTimeSeconds": task["issued_at"] + 20,
                                         "verdict": "COMPILATION_ERROR",
                                         "problem": {"contestId": int(key[:-1]), "index": key[-1]}}])
    frozen_now.t += 30
    r = client.post("/api/auth/check", json={"task_id": task["task_id"]}).json()
    assert r["status"] == "verified"
    me = client.get("/api/me").json()
    assert me["user"]["handle"] == "alice" and me["active"] is None
    client.post("/api/auth/logout")
    assert client.get("/api/me").json() == {"user": None}


def test_pages_served(client):
    assert client.get("/login").status_code == 200
    assert client.get("/static/css/tokens.css").status_code == 200


def test_match_pages_and_timeline_hidden_until_final(client):
    from app import db
    db.execute("INSERT INTO matches(id, code, mode, rules, status, result_status, settings, created_at, start_time, end_time)"
               " VALUES (1,'QQQQQQ','friend','icpc','ended','pending','{}',0,0,10)")
    assert client.get("/m/QQQQQQ").status_code == 200
    assert client.get("/api/matches/QQQQQQ/timeline").status_code == 409
    assert client.get("/api/matches/NOPE00").status_code == 404
