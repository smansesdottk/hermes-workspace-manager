import pytest
import os
import app as panel
import manager


def _csrf(c, path="/dashboard"):
    page = c.get(path, follow_redirects=True).get_data(as_text=True)
    import re
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
    return m.group(1) if m else ""


def test_instance_soul_update_and_read(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path))
    inst_dir = tmp_path / "testinst" / "hermes-data"
    inst_dir.mkdir(parents=True)
    
    assert manager.get_instance_soul("testinst") == ""
    manager.set_instance_soul("testinst", "Anda adalah asisten super cerdas.")
    assert manager.get_instance_soul("testinst") == "Anda adalah asisten super cerdas."


def test_list_audit_returns_recent_actions():
    manager.audit("test_actor", "test_action", "test_detail")
    audits = manager.list_audit(10)
    assert any(a["actor"] == "test_actor" and a["action"] == "test_action" for a in audits)
