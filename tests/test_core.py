#!/usr/bin/env python3
"""Unit tests logika inti dockermanagement. Jalankan: python3 -m pytest tests/ -q"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docker_ops
import manager


@pytest.fixture()
def tmp_env(monkeypatch):
    d = tempfile.mkdtemp(prefix="dm-test-")
    monkeypatch.setattr(manager, "BASE_DIR", d)
    monkeypatch.setattr(manager, "INSTANCES_DIR", os.path.join(d, "instances"))
    monkeypatch.setattr(manager, "DB_PATH", os.path.join(d, "panel.db"))
    os.makedirs(os.path.join(d, "instances"), exist_ok=True)
    manager.init_db()
    yield d


def test_slug_validation():
    assert docker_ops.validate_slug("budi") == "budi"
    with pytest.raises(ValueError):
        docker_ops.validate_slug("Bad Name")
    with pytest.raises(ValueError):
        docker_ops.validate_slug("../etc")
    with pytest.raises(ValueError):
        docker_ops.validate_slug("a")


def test_create_user_and_login(tmp_env):
    uid, pw = manager.create_user("budi", None, "user")
    assert uid > 0 and len(pw) >= 8
    u = manager.get_user("BUDI")  # case-insensitive
    assert u and u["role"] == "user"
    with pytest.raises(ValueError):
        manager.create_user("x", None, "user")  # terlalu pendek
    with pytest.raises(ValueError):
        manager.create_user("okname", "short", "user")  # password pendek


def test_ports_unique(tmp_env):
    p1 = manager.next_ports()
    p2 = manager.next_ports()  # belum ada instance, boleh sama (belum terpakai di DB)
    assert isinstance(p1, tuple)
    uid, _ = manager.create_user("u1", None, "user")
    info = manager.create_instance("alpha", uid, dash_password="passwordku123")
    assert info["hermes_port"] >= 21000
    used = manager.used_ports()
    assert info["hermes_port"] in used and info["router_port"] in used
    manager.destroy_instance("alpha", wipe_data=True)


def test_create_instance_renders_compose(tmp_env):
    uid, _ = manager.create_user("u2", None, "user")
    info = manager.create_instance("beta", uid, dash_password="passwordku123")
    path = os.path.join(tmp_env, "instances", "beta", "compose.yaml")
    assert os.path.exists(path)
    text = open(path).read()
    assert "dockerman-beta-hermes" in text
    assert f"127.0.0.1:{info['hermes_port']}:9119" in text
    assert 'mem_limit: 1500m' in text  # resource limit ada
    assert "INITIAL_PASSWORD" in text and info["router_password"] in text
    # ownership check
    assert manager.get_instance_for_user("beta", uid, False)["slug"] == "beta"
    other_uid, _ = manager.create_user("u3", None, "user")
    assert manager.get_instance_for_user("beta", other_uid, False) is None
    assert manager.get_instance_for_user("beta", other_uid, True)["slug"] == "beta"
    manager.destroy_instance("beta", wipe_data=True)


def test_duplicate_instance_rejected(tmp_env):
    uid, _ = manager.create_user("u4", None, "user")
    manager.create_instance("gamma", uid, dash_password="passwordku123")
    with pytest.raises(ValueError):
        manager.create_instance("gamma", uid, dash_password="passwordku123")
    manager.destroy_instance("gamma", wipe_data=True)


def test_destroy_removes_record_and_data(tmp_env):
    uid, _ = manager.create_user("u5", None, "user")
    # tanpa docker daemon pun, destroy harus membersihkan record DB & folder
    import docker_ops as dop

    orig = dop.compose

    def fake_compose(project_dir, *a, **kw):
        return {"ok": True, "out": "", "err": "", "code": 0}

    dop.compose = fake_compose
    original_key = manager._create_router_key
    manager._create_router_key = lambda *args: "test-key"
    try:
        manager.create_instance("delta", uid, dash_password="passwordku123")
        dpath = manager.instance_dir("delta")
        assert os.path.isdir(dpath)
        manager.destroy_instance("delta", wipe_data=True)
        assert not os.path.exists(dpath)
        assert manager.get_instance_by_slug("delta") is None
    finally:
        dop.compose = orig
        manager._create_router_key = original_key


def test_audit_written(tmp_env):
    manager.audit("tester", "unit_test", "detail")
    with manager.db() as c:
        n = c.execute("SELECT COUNT(*) n FROM audit WHERE action='unit_test'").fetchone()["n"]
    assert n == 1
