import os
import sys

from werkzeug.security import check_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as panel
import manager


def test_whatsapp_apply_allows_linked_account(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "dashboard_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"id": 7, "username": "owner"}, inst))
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)
    calls = []
    def hermes(_, method, path, body=None):
        calls.append((method, path, body))
        if method == "get": return {"status": "connected", "account_phone": "628123"}
        return {"ok": True, "restart_started": True}
    monkeypatch.setattr(panel, "_hermes_json", hermes)
    with panel.app.test_client() as client:
        with client.session_transaction() as session:
            session["wa:demo"] = {"id": "pair", "expires": 9999999999}
        result = client.get("/i/demo/whatsapp/onboarding/status")
    assert result.json["status"] == "connected"
    assert calls[-1] == ("post", "/api/messaging/whatsapp/onboarding/pair/apply", {"mode": "bot", "allowed_users": "628123"})


def test_channel_status_and_toggle_restart(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "dashboard_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"id": 7, "username": "owner"}, inst))
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)
    calls = []
    def hermes(_, method, path, body=None):
        calls.append((method, path, body))
        if path == "/api/messaging/platforms":
            return {"platforms": [{"id": "telegram", "enabled": True, "configured": True, "state": "connected"}]}
        return {"ok": True}
    monkeypatch.setattr(panel, "_hermes_json", hermes)
    with panel.app.test_client() as client:
        assert client.get("/i/demo/channels").json["platforms"][0]["state"] == "connected"
        assert client.post("/i/demo/channels/telegram", json={"enabled": False}).json["ok"]
        assert client.post("/i/demo/gateway/restart").json["ok"]
    assert ("put", "/api/messaging/platforms/telegram", {"enabled": False}) in calls
    assert ("post", "/api/gateway/restart", {}) in calls


def test_user_can_change_own_password(monkeypatch, tmp_path):
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    manager.init_db()
    uid, _ = manager.create_user("owner", "old-password", "user")
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with panel.app.test_client() as client:
        with client.session_transaction() as session: session["uid"] = uid
        assert client.post("/account/password", data={"current_password": "old-password", "new_password": "new-password"}).status_code == 302
    assert check_password_hash(manager.get_user("owner")["password_hash"], "new-password")
