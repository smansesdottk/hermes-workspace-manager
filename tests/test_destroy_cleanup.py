import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import manager
import tunnel_supervisor


def test_destroy_keeps_data_and_record_when_docker_cleanup_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    os.makedirs(manager.instance_dir("demo"))
    open(os.path.join(manager.instance_dir("demo"), "compose.yaml"), "w").close()
    manager.init_db()
    owner, _ = manager.create_user("owner", None, "user")
    with manager.db() as c:
        c.execute("""INSERT INTO instances(slug,owner_id,hermes_port,router_port,status,dashboard_password,router_password,auth_secret,created_at)
                     VALUES('demo',?,21000,21001,'running','x','x','x','now')""", (owner,))
    monkeypatch.setattr(manager.docker_ops, "compose", lambda *a, **k: {"ok": False, "err": "down failed"})

    with pytest.raises(RuntimeError):
        manager.destroy_instance("demo", wipe_data=True)
    assert os.path.isdir(manager.instance_dir("demo"))
    assert manager.get_instance_by_slug("demo") is not None


def test_destroy_cleans_orphan_record_when_containers_are_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    manager.init_db()
    owner, _ = manager.create_user("owner", None, "user")
    with manager.db() as c:
        c.execute("""INSERT INTO instances(slug,owner_id,hermes_port,router_port,status,dashboard_password,router_password,auth_secret,created_at)
                     VALUES('orphan',?,21000,21001,'running','x','x','x','now')""", (owner,))
    monkeypatch.setattr(manager.docker_ops, "inspect_json", lambda name: None)

    assert manager.destroy_instance("orphan", wipe_data=True)["ok"]
    assert manager.get_instance_by_slug("orphan") is None


def test_reconcile_stops_tunnel_for_deleted_instance(monkeypatch):
    class Child:
        def __init__(self): self.terminated = False
        def poll(self): return None
        def terminate(self): self.terminated = True

    child = Child()
    monkeypatch.setattr(tunnel_supervisor.manager, "list_instances", lambda: [])
    tunnel_supervisor.CHILDREN.clear()
    tunnel_supervisor.CHILDREN[("deleted", "hermes")] = child
    tunnel_supervisor.reconcile_once()
    assert child.terminated
    assert not tunnel_supervisor.CHILDREN
