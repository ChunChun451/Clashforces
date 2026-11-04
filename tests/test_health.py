from app import db


def _match(code: str, status: str) -> None:
    db.execute("INSERT INTO matches(code, mode, rules, status, settings, created_at) VALUES (?,'friend','icpc',?,'{}',0)",
               (code, status))


def test_health_counts_running_matches(client):
    body = client.get("/api/health").json()
    assert body["running_matches"] == 0
    for code, status in [("AAAAAA", "lobby"), ("BBBBBB", "starting"), ("CCCCCC", "running"),
                         ("DDDDDD", "ended"), ("EEEEEE", "cancelled")]:
        _match(code, status)
    body = client.get("/api/health").json()
    assert body["running_matches"] == 2
    assert body["cf_api"] == "ok"


def test_static_files_are_revalidated(tmp_path, monkeypatch, stub_cf):
    """After a deploy nobody keeps old CSS/JS: browsers must check before reusing a cached copy."""
    from fastapi.testclient import TestClient

    from app import config, db
    monkeypatch.setattr(config, "BACKGROUND_TASKS", False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "s.db"))
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/static/css/players.css")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
        assert c.get("/players").headers["cache-control"] == "no-cache"
        assert c.get("/static/css/players.css", headers={"if-none-match": r.headers["etag"]}).status_code == 304
    db._conn = None
