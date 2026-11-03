"""DEV_AUTO_LOGIN signs localhost visitors in, and nobody else."""

import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.auth import users


@pytest.fixture
def app_with_user(tmp_path, monkeypatch, stub_cf):
    monkeypatch.setattr(config, "BACKGROUND_TASKS", False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setattr(config, "DEV_AUTO_LOGIN", "GandalfTheGay")
    from app.main import app
    yield app
    db._conn = None


def me(app, base_url, host):
    with TestClient(app, base_url=base_url, client=(host, 50000)) as c:
        if users.by_handle("GandalfTheGay") is None:
            users.create("GandalfTheGay", 1500, 1600)
        return c.get("/api/me").json()


def test_localhost_is_signed_in(app_with_user):
    body = me(app_with_user, "http://localhost:8000", "127.0.0.1")
    assert body["user"]["handle"] == "GandalfTheGay"


def test_other_hosts_are_not(app_with_user):
    assert me(app_with_user, "http://localhost:8000", "10.0.0.5")["user"] is None
    assert me(app_with_user, "https://clashforces.duckdns.org", "127.0.0.1")["user"] is None


def test_refused_with_secure_cookies(monkeypatch):
    monkeypatch.setattr(config, "COOKIE_SECURE", True)
    monkeypatch.setattr(config, "SECRET_KEY", "something-long-and-random")
    monkeypatch.setattr(config, "DEV_AUTO_LOGIN", "GandalfTheGay")
    with pytest.raises(RuntimeError, match="DEV_AUTO_LOGIN"):
        config.check_production_settings()
