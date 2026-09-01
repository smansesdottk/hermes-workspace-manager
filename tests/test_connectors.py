import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as panel


def test_whatsapp_qr_is_kept_in_panel_session(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "dashboard_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"username": "owner"}, inst))
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)
    monkeypatch.setattr(panel, "_hermes_json", lambda *args: {"pairing_id": "pair-1", "qr_payload": "hello", "status": "waiting"})
    with panel.app.test_client() as client:
        response = client.post("/i/demo/whatsapp/onboarding")
        assert response.status_code == 200
        assert response.json["qr"].startswith("data:image/png;base64,")
        with client.session_transaction() as sess:
            assert sess["wa:demo"]["id"] == "pair-1"


def test_whatsapp_connected_applies_then_reports_gateway_restart(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "dashboard_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"username": "owner"}, inst))
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)
    calls = []
    def hermes(_inst, method, path, body=None):
        calls.append((method, path, body))
        if method == "get": return {"status": "connected"}
        return {"ok": True, "restart_started": True}
    monkeypatch.setattr(panel, "_hermes_json", hermes)
    with panel.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["wa:demo"] = {"id": "pair-1", "expires": 9999999999}
        response = client.get("/i/demo/whatsapp/onboarding/status")
    assert response.json["status"] == "connected"
    assert response.json["restart_started"] is True
    assert calls[-1] == ("post", "/api/messaging/whatsapp/onboarding/pair-1/apply", {"mode": "bot", "allowed_users": ""})


def test_telegram_manual_rejects_non_numeric_owner(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "dashboard_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"username": "owner"}, inst))
    with panel.app.test_client() as client:
        response = client.post("/i/demo/telegram/manual", data={"token": "token", "allowed_user": "not-id"})
        assert response.status_code == 302
        with client.session_transaction() as sess:
            assert "_flashes" in sess
