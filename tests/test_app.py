import json
import os
import sqlite3
import subprocess
import tempfile

import pytest
import requests

os.environ["APP_PASSWORD"] = "secret"
os.environ["DB_FILE"] = os.path.join(tempfile.mkdtemp(), "app.db")

from fastapi.testclient import TestClient

import app

DENY = "domains/deny/exact"


class FakePihole:
    """In-memory stand-in for the Pi-hole v6 API used by app.py."""

    def __init__(self):
        self.deny = {}
        self.calls = []
        self.fail = False
        self.expired_sids = set()

    def request(self, method, url, headers, timeout, json=None):
        path = url.split("/api/", 1)[1]
        self.calls.append((method, path))

        if headers["X-FTL-SID"] in self.expired_sids:
            return response(401, {"error": {"key": "unauthorized"}})

        if self.fail:
            return response(400, {"error": {"key": "database_error"}})

        if path == "groups":
            return response(200, {"groups": [{"id": 0, "name": "Default"}]})

        if path == DENY:
            return response(200, {"domains": list(self.deny.values())})

        domain = path.removeprefix(DENY + "/")

        if method == "PUT":
            self.deny[domain] = {"domain": domain, **json}
            return response(200, {})

        if method == "DELETE":
            self.deny.pop(domain, None)
            return response(204, None)

        raise AssertionError(f"unexpected call {method} {path}")


def response(status, body):
    r = requests.Response()
    r.status_code = status
    r._content = b"" if body is None else json.dumps(body).encode()
    r.url = "http://pihole"
    return r


@pytest.fixture
def pihole(monkeypatch):
    fake = FakePihole()
    monkeypatch.setattr(app.requests, "request", fake.request)
    monkeypatch.setattr(app, "pihole_login", lambda: "sid")
    monkeypatch.setattr(app, "PIHOLE_SID", None)
    return fake


@pytest.fixture
def client():
    conn = sqlite3.connect(app.DB_FILE)
    conn.executescript(
        """
        delete from services;
        delete from service_domains;
        delete from state;
        delete from service_groups;
        delete from catalog_meta;

        insert into services values
            ('tiktok', 'TikTok', 'social_network'),
            ('douyin', 'Douyin', 'social_network');

        insert into service_domains values
            ('tiktok', 'tiktok.com'),
            ('tiktok', 'shared.com'),
            ('douyin', 'douyin.com'),
            ('douyin', 'shared.com');
        """
    )
    conn.commit()
    conn.close()
    test_client = TestClient(app.app)
    test_client.auth = ("admin", "secret")
    return test_client


def toggle(client, service_id):
    return client.post(f"/api/toggle/{service_id}").json()


def test_fresh_database_has_tables(client):
    data = client.get("/api/services").json()

    assert data["stats"]["services"] == 2


def test_toggle_blocks_and_unblocks_for_default_group(client, pihole):
    assert toggle(client, "tiktok")["enabled"] is True
    assert pihole.deny["tiktok.com"]["groups"] == [0]
    assert pihole.deny["tiktok.com"]["comment"] == app.COMMENT

    assert toggle(client, "tiktok")["enabled"] is False
    assert pihole.deny == {}


def test_disable_keeps_entries_the_app_did_not_create(client, pihole):
    manual = {"domain": "tiktok.com", "comment": "mine", "groups": [0],
              "enabled": True}
    pihole.deny["tiktok.com"] = manual

    toggle(client, "tiktok")
    toggle(client, "tiktok")

    assert pihole.deny == {"tiktok.com": manual}


def test_disable_keeps_domain_still_used_by_another_service(client, pihole):
    toggle(client, "tiktok")
    toggle(client, "douyin")
    toggle(client, "tiktok")

    assert set(pihole.deny) == {"douyin.com", "shared.com"}


def test_pihole_error_is_reported_and_state_not_saved(client, pihole):
    pihole.fail = True

    result = toggle(client, "tiktok")

    assert result["success"] is False
    services = client.get("/api/services").json()["services"]
    assert all(not s["enabled"] for s in services)


def test_expired_session_logs_in_again_once(client, pihole, monkeypatch):
    monkeypatch.setattr(app, "PIHOLE_SID", "old")
    pihole.expired_sids.add("old")

    assert toggle(client, "tiktok")["success"] is True
    assert pihole.calls[:2] == [("GET", DENY), ("GET", DENY)]


def test_saving_groups_moves_a_blocked_service(client, pihole):
    toggle(client, "tiktok")

    saved = client.post("/api/service/tiktok/groups", json={"groups": [2]})

    assert saved.json()["success"] is True
    assert pihole.deny["tiktok.com"]["groups"] == [2]


def test_saving_groups_for_unblocked_service_leaves_pihole_alone(
    client, pihole
):
    client.post("/api/service/tiktok/groups", json={"groups": [2]})

    assert pihole.calls == []


def test_catalog_update_unblocks_dropped_domains_and_services(
    client, pihole, monkeypatch
):
    toggle(client, "tiktok")
    toggle(client, "douyin")

    def fake_catalog_update(*args, **kwargs):
        conn = sqlite3.connect(app.DB_FILE)
        conn.executescript(
            """
            delete from services where id = 'douyin';
            delete from service_domains where service_id = 'douyin';
            delete from service_domains where domain = 'tiktok.com';
            insert into service_domains values ('tiktok', 'tiktokv.com');
            """
        )
        conn.commit()
        conn.close()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(app.subprocess, "run", fake_catalog_update)

    result = client.post("/api/catalog/update").json()

    assert result["success"] is True
    assert set(pihole.deny) == {"shared.com", "tiktokv.com"}
    stats = client.get("/api/services").json()["stats"]
    assert stats["enabled"] == 1


def test_requests_without_the_password_are_refused(client):
    assert client.get("/", auth=("admin", "wrong")).status_code == 401
    assert TestClient(app.app).get("/api/services").status_code == 401


def test_cross_site_post_is_refused(client, pihole):
    evil = {"Origin": "http://evil.example"}

    result = client.post("/api/toggle/tiktok", headers=evil)

    assert result.status_code == 403
    assert pihole.calls == []


def test_same_site_post_is_allowed(client, pihole):
    same = {"Origin": "http://testserver"}

    result = client.post("/api/toggle/tiktok", headers=same)

    assert result.json()["success"] is True


def test_service_list_includes_groups(client):
    client.post("/api/service/tiktok/groups", json={"groups": [3, 1]})

    services = client.get("/api/services").json()["services"]
    by_id = {s["id"]: s for s in services}

    assert by_id["tiktok"]["groups"] == [1, 3]
    assert by_id["douyin"]["groups"] == []
