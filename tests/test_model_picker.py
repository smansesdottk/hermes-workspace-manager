import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as panel


def test_router_models_uses_authenticated_openai_catalog(monkeypatch, tmp_path):
    instance_dir = tmp_path / "demo" / "hermes-data"
    instance_dir.mkdir(parents=True)
    (instance_dir / ".env").write_text("HERMES_CUSTOM_9ROUTER_20128_API_KEY=test-key\n")
    monkeypatch.setattr(panel.manager, "instance_dir", lambda _: str(tmp_path / "demo"))
    calls = []

    class Response:
        ok = True
        def json(self): return {"data": [{"id": "active/model"}]}
    class Client:
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()
    monkeypatch.setattr(panel.requests, "Session", Client)

    assert panel._router_models({"slug": "demo", "router_port": 21001}) == ["active/model"]
    assert calls == [("http://127.0.0.1:21001/v1/models", {"headers": {"Authorization": "Bearer test-key"}, "timeout": 20})]


def test_picker_proxies_hermes_options_and_enriches_own_9router(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "router_port": 21001,
            "dashboard_password": "secret", "router_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"username": "owner"}, inst))
    monkeypatch.setattr(panel, "_router_models", lambda _: ["provider/model-a"])
    monkeypatch.setattr(panel, "_hermes_json", lambda *args: {
        "providers": [{"slug": "custom:9router", "name": "9Router", "models": ["old"]},
                      {"slug": "openrouter", "name": "OpenRouter", "models": ["x"]}],
        "provider": "custom:9router", "model": "provider/model-a"})

    with panel.app.test_client() as client:
        response = client.get("/i/demo/models")

    assert response.status_code == 200
    assert response.json["providers"][0]["models"] == ["provider/model-a"]


def test_picker_saves_the_provider_and_model_hermes_offered(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "router_port": 21001,
            "dashboard_password": "secret", "router_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"username": "owner"}, inst))
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)
    monkeypatch.setattr(panel, "_picker_options", lambda _: {"providers": [
        {"slug": "custom:9router", "models": ["provider/model-a"], "api_url": "http://9router:20128/v1", "authenticated": True}]})
    calls = []
    monkeypatch.setattr(panel, "_hermes_json", lambda *args: calls.append(args) or {"ok": True})

    with panel.app.test_client() as client:
        response = client.post("/i/demo/model", json={"provider": "custom:9router", "model": "provider/model-a"})

    assert response.status_code == 200
    assert calls[0][1:] == ("post", "/api/model/set", {
        "scope": "main", "provider": "custom:9router", "model": "provider/model-a",
        "base_url": "http://9router:20128/v1",
    })


def test_picker_rejects_model_not_offered_by_hermes(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "router_port": 21001,
            "dashboard_password": "secret", "router_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"username": "owner"}, inst))
    monkeypatch.setattr(panel, "_picker_options", lambda _: {"providers": [
        {"slug": "custom:9router", "models": ["provider/model-a"]}]})

    with panel.app.test_client() as client:
        response = client.post("/i/demo/model", json={"provider": "custom:9router", "model": "other/model"})

    assert response.status_code == 400
    assert response.json["error"] == "Model tidak tersedia untuk provider tersebut."


def test_provider_key_is_forwarded_only_to_owned_hermes(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo", "hermes_port": 21000, "dashboard_password": "secret"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"username": "owner"}, inst))
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)
    monkeypatch.setattr(panel, "_picker_options", lambda _: {"providers": [
        {"slug": "openrouter", "key_env": "OPENROUTER_API_KEY"}]})
    calls = []
    monkeypatch.setattr(panel, "_hermes_json", lambda *args: calls.append(args) or {"ok": True})

    with panel.app.test_client() as client:
        response = client.post("/i/demo/model/key", json={"provider": "openrouter", "key": "test-key"})

    assert response.status_code == 200
    assert calls[0][1:] == ("put", "/api/env", {"key": "OPENROUTER_API_KEY", "value": "test-key"})
    assert "test-key" not in response.get_data(as_text=True)
