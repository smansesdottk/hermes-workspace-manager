#!/usr/bin/env python3
"""Regression tests for public Quick Tunnel URLs on each instance."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import manager


@pytest.fixture()
def tmp_env(monkeypatch):
    d = tempfile.mkdtemp(prefix="dm-tunnel-test-")
    monkeypatch.setattr(manager, "BASE_DIR", d)
    monkeypatch.setattr(manager, "INSTANCES_DIR", os.path.join(d, "instances"))
    monkeypatch.setattr(manager, "DB_PATH", os.path.join(d, "panel.db"))
    os.makedirs(manager.INSTANCES_DIR, exist_ok=True)
    manager.init_db()
    yield d


def test_tunnel_urls_are_persisted_and_replaced(tmp_env):
    uid, _ = manager.create_user("budi", None, "user")
    with manager.db() as c:
        c.execute("""INSERT INTO instances(slug,owner_id,hermes_port,router_port,status,
                   dashboard_password,router_password,auth_secret,created_at)
                   VALUES('budi', ?, 21000, 21001, 'running','x','y','z','now')""", (uid,))

    manager.set_tunnel_url("budi", "hermes", "https://lama.trycloudflare.com")
    manager.set_tunnel_url("budi", "9router", "https://router.trycloudflare.com")
    row = manager.get_instance_by_slug("budi")
    assert row["hermes_url"] == "https://lama.trycloudflare.com"
    assert row["router_url"] == "https://router.trycloudflare.com"

    manager.set_tunnel_url("budi", "hermes", "https://baru.trycloudflare.com")
    assert manager.get_instance_by_slug("budi")["hermes_url"] == "https://baru.trycloudflare.com"


def test_tunnel_url_rejects_non_cloudflare_url(tmp_env):
    with pytest.raises(ValueError):
        manager.validate_tunnel_url("http://127.0.0.1:21000")
    assert manager.validate_tunnel_url("https://valid-name.trycloudflare.com") == "https://valid-name.trycloudflare.com"
