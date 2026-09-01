import pytest
import os
import app as panel
import manager


def _csrf(c, path="/dashboard"):
    page = c.get(path, follow_redirects=True).get_data(as_text=True)
    import re
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
    return m.group(1) if m else ""


def test_quick_set_model_success(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path))
    inst_dir = tmp_path / "testinst" / "hermes-data"
    inst_dir.mkdir(parents=True)
    cfg_file = inst_dir / "config.yaml"
    cfg_file.write_text("model:\n  default: old-model\n")
    
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"id": 1, "username": "admin"}, {"slug": "testinst"}))
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)
    
    with panel.app.test_client() as client:
        res = client.post("/i/testinst/models/quick-set", json={"model": "qd/qmodel_38max"})
        assert res.status_code == 200
        assert res.json["ok"] is True
        assert res.json["model"] == "qd/qmodel_38max"
        
    m = manager.get_instance_active_model("testinst")
    assert m["model"] == "qd/qmodel_38max"
