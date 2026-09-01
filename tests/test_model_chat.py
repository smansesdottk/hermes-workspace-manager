import pytest
import os
import re
import sys
import app as panel
import manager


def _csrf(c, path="/dashboard"):
    page = c.get(path, follow_redirects=True).get_data(as_text=True)
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
    return m.group(1) if m else ""


def test_get_instance_active_model_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path))
    m = manager.get_instance_active_model("nonexistent")
    assert m["model"] == "oc/muse-spark-1.2-contributor-free"


def test_get_instance_active_model_from_config(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path))
    inst_dir = tmp_path / "testinst" / "hermes-data"
    inst_dir.mkdir(parents=True)
    cfg = inst_dir / "config.yaml"
    cfg.write_text("model:\n  default: anthropic/claude-sonnet-4\n  provider: anthropic\n")
    
    m = manager.get_instance_active_model("testinst")
    assert m["model"] == "anthropic/claude-sonnet-4"
    assert m["provider"] == "anthropic"


def test_test_chat_route_requires_auth():
    with panel.app.test_client() as c:
        res = c.post("/i/budi/model/chat", json={"message": "Halo"})
        assert res.status_code in (302, 400, 401)


def test_test_chat_route_validates_input(monkeypatch):
    with panel.app.test_client() as c:
        inst = {"slug": "budi", "router_port": 21000}
        monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"id": 7, "username": "budi"}, inst))
        
        # Empty message
        dash_token = _csrf(c, "/dashboard")
        res = c.post("/i/budi/model/chat", json={"message": ""}, headers={"X-CSRFToken": dash_token})
        assert res.status_code == 400
        
        # Mocking manager.test_chat_instance
        monkeypatch.setattr(manager, "test_chat_instance", lambda slug, msg: {"ok": True, "model": "test-model", "reply": "Halo juga!"})
        res2 = c.post("/i/budi/model/chat", json={"message": "Halo"}, headers={"X-CSRFToken": dash_token})
        assert res2.status_code == 200
        assert res2.get_json()["reply"] == "Halo juga!"


