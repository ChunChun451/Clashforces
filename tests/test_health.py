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
