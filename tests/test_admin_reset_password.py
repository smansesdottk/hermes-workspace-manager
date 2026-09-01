import re
import pytest
import app as panel
import manager


def _csrf(c, path="/dashboard"):
    page = c.get(path, follow_redirects=True).get_data(as_text=True)
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
    return m.group(1) if m else ""


def test_admin_reset_password_generates_new_pass_and_flashes(monkeypatch, tmp_path):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    
    # Mock admin user & target user
    admin = {"id": 1, "username": "admin", "role": "admin"}
    target = {"id": 2, "username": "budi", "role": "user"}
    
    monkeypatch.setattr(panel, "current_user", lambda: admin)
    monkeypatch.setattr(panel, "require_admin", lambda: admin)
    monkeypatch.setattr(manager, "reset_password", lambda uid: "pass-baru-12345")
    monkeypatch.setattr(manager, "audit", lambda *args: None)
    
    with panel.app.test_client() as client:
        res = client.post("/admin/users/2/reset-password", follow_redirects=True)
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "Password baru: pass-baru-12345" in html
