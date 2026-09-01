#!/usr/bin/env python3
"""Regression: instance_action harus KONVERGEN — kedua kontainer hidup
meski `docker compose start` intermiten hanya men-start satu kontainer
(bug compose v2.31 yang direproduksi live).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docker_ops
import manager


@pytest.fixture()
def tmp_env(monkeypatch):
    import tempfile

    d = tempfile.mkdtemp(prefix="dm-test-")
    monkeypatch.setattr(manager, "BASE_DIR", d)
    monkeypatch.setattr(manager, "INSTANCES_DIR", os.path.join(d, "instances"))
    monkeypatch.setattr(manager, "DB_PATH", os.path.join(d, "panel.db"))
    os.makedirs(os.path.join(d, "instances"), exist_ok=True)
    manager.init_db()
    yield d


class _FlakyCompose:
    """Simulasi bug compose v2.31: satu panggilan `start` hanya men-start
    SATU kontainer yang masih mati."""

    def __init__(self):
        self.up = {"hermes": False, "9router": False}

    def start_one(self, instance, service, timeout=120):
        """Simulasi `docker start <container>`: langsung menghidupkan kontainer tsb."""
        self.up[service] = True
        return {"ok": True, "out": f"{instance}-{service}\n", "err": "", "code": 0}

    def compose(self, project_dir, *args, **kw):
        cmd = args[0] if args else ""
        if cmd == "up":  # provisioning awal: anggap semua hidup
            self.up = {"hermes": True, "9router": True}
            return {"ok": True, "out": "", "err": "", "code": 0}
        if cmd == "stop":
            self.up = {"hermes": False, "9router": False}
            return {"ok": True, "out": "", "err": "", "code": 0}
        if cmd == "start":
            for svc in ("hermes", "9router"):
                if not self.up[svc]:
                    self.up[svc] = True
                    return {"ok": True, "out": "",
                            "err": f"Container dockerman-x-{svc}  Started\n", "code": 0}
            return {"ok": True, "out": "", "err": "", "code": 0}
        return {"ok": True, "out": "", "err": "", "code": 0}


def _setup_instance(tmp_env, monkeypatch, flaky):
    uid, _ = manager.create_user("u1", None, "user")
    monkeypatch.setattr(docker_ops, "compose", flaky.compose)
    monkeypatch.setattr(docker_ops, "start_one", flaky.start_one)
    monkeypatch.setattr(docker_ops, "ps_one",
                        lambda slug, svc: flaky.up[svc])
    orig_key = manager._create_router_key
    manager._create_router_key = lambda *a: "test-key"
    try:
        manager.create_instance("alpha", uid, dash_password="passwordku123")
    finally:
        manager._create_router_key = orig_key


def test_restart_converges_when_compose_start_partial(tmp_env, monkeypatch):
    flaky = _FlakyCompose()
    _setup_instance(tmp_env, monkeypatch, flaky)

    manager.instance_action("alpha", "restart")

    # Kedua kontainer HARUS benar-benar hidup setelah restart,
    # apa pun keisengian compose.
    assert flaky.up == {"hermes": True, "9router": True}


def test_restart_status_reflects_real_state(tmp_env, monkeypatch):
    flaky = _FlakyCompose()
    _setup_instance(tmp_env, monkeypatch, flaky)

    manager.instance_action("alpha", "restart")

    with manager.db() as c:
        row = c.execute("SELECT status FROM instances WHERE slug='alpha'").fetchone()
    assert row["status"] == "running"


def test_start_failure_does_not_report_running(tmp_env, monkeypatch):
    """Kalau kontainer tetap mati walau exit-code OK, status jujur = stopped/partial."""
    class _Dead(_FlakyCompose):
        def compose(self, project_dir, *args, **kw):
            r = super().compose(project_dir, *args, **kw)
            if args and args[0] == "stop":
                pass
            return r

    flaky = _Dead()
    # paksa: start tidak pernah menghidupkan apapun
    flaky.up = {"hermes": True, "9router": True}  # setelah provisioning

    def dead_compose(project_dir, *args, **kw):
        cmd = args[0] if args else ""
        if cmd == "stop":
            flaky.up = {"hermes": False, "9router": False}
        return {"ok": True, "out": "", "err": "", "code": 0}

    uid, _ = manager.create_user("u2", None, "user")
    monkeypatch.setattr(docker_ops, "compose", dead_compose)
    monkeypatch.setattr(docker_ops, "ps_one", lambda slug, svc: flaky.up[svc])
    orig_key = manager._create_router_key
    manager._create_router_key = lambda *a: "test-key"
    try:
        manager.create_instance("beta", uid, dash_password="passwordku123")
    finally:
        manager._create_router_key = orig_key

    manager.instance_action("beta", "restart")

    with manager.db() as c:
        row = c.execute("SELECT status FROM instances WHERE slug='beta'").fetchone()
    assert row["status"] != "running"
