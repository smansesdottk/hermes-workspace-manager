#!/usr/bin/env python3
"""Perilaku provisioning dan pembatasan resource instance."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import manager


@pytest.fixture()
def tmp_env(monkeypatch):
    d = tempfile.mkdtemp(prefix="dm-provision-")
    monkeypatch.setattr(manager, "BASE_DIR", d)
    monkeypatch.setattr(manager, "INSTANCES_DIR", os.path.join(d, "instances"))
    monkeypatch.setattr(manager, "DB_PATH", os.path.join(d, "panel.db"))
    os.makedirs(manager.INSTANCES_DIR)
    manager.init_db()
    yield d


def test_instance_compose_has_sane_default_limits_and_named_router_endpoint(tmp_env, monkeypatch):
    monkeypatch.setattr(manager.docker_ops, "compose", lambda *a, **k: {"ok": True, "out": "", "err": ""})
    monkeypatch.setattr(manager, "_create_router_key", lambda *args: "test-key")
    owner, _ = manager.create_user("owner", None, "user")
    manager.create_instance("limits", owner)
    text = open(os.path.join(manager.instance_dir("limits"), "compose.yaml")).read()
    assert "cpus: '1.00'" in text
    assert "mem_limit: 1500m" in text
    assert "storage_opt:" not in text  # host overlayfs lacks XFS project quota support
    config = open(os.path.join(manager.instance_dir("limits"), "hermes-data", "config.yaml")).read()
    assert "name: 9Router" in config
    assert "base_url: http://9router:20128/v1" in config
    assert "model: oc/hy3-free" in config
    assert "HERMES_INFERENCE_MODEL" not in text


def test_new_instance_creates_own_router_key_and_wires_hermes(tmp_env, monkeypatch):
    calls = []
    monkeypatch.setattr(manager.docker_ops, "compose", lambda *a, **k: calls.append(a[1:]) or {"ok": True, "out": "", "err": ""})

    class Response:
        ok = True
        def json(self): return {"key": "instance-only-key"}
    class Client:
        def post(self, *args, **kwargs): return Response()
    monkeypatch.setattr(manager.requests, "Session", Client)
    owner, _ = manager.create_user("key-owner", None, "user")
    manager.create_instance("keyed", owner)

    data_dir = os.path.join(manager.instance_dir("keyed"), "hermes-data")
    assert open(os.path.join(data_dir, ".env")).read() == "HERMES_CUSTOM_9ROUTER_20128_API_KEY=instance-only-key\n"
    config = open(os.path.join(data_dir, "config.yaml")).read()
    assert "key_env: HERMES_CUSTOM_9ROUTER_20128_API_KEY" in config
    assert calls[-1] == ("restart", "hermes")


def test_admin_resource_update_only_accepts_bounded_values(tmp_env):
    owner, _ = manager.create_user("limits-owner", None, "user")
    with pytest.raises(ValueError):
        manager.validate_resource_limits("0.10", "200m", "1G")
    assert manager.validate_resource_limits("2.00", "2g", "12G") == ("2.00", "2g", "12G")


def test_owner_cannot_read_other_instance(tmp_env):
    one, _ = manager.create_user("owner-one", None, "user")
    two, _ = manager.create_user("owner-two", None, "user")
    assert manager.get_instance_for_user("missing", one, False) is None
    with manager.db() as c:
        c.execute("INSERT INTO instances(slug,owner_id,hermes_port,router_port,status,dashboard_password,router_password,auth_secret,created_at) VALUES(?,?,?,?,?,?,?,?,?)", ("private", one, 21000, 21001, "created", "x", "x", "x", manager.now()))
    assert manager.get_instance_for_user("private", two, False) is None
    assert manager.get_instance_for_user("private", two, True)["slug"] == "private"
