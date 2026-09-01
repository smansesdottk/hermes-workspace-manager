import app as panel
import docker_ops


def test_instance_terminal_starts_only_owned_container(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"id": 7, "username": "owner"}, inst))
    calls = []
    monkeypatch.setattr(panel.docker_ops, "start_instance_terminal", lambda slug: calls.append(slug) or "run-1")
    monkeypatch.setattr(panel.manager, "audit", lambda *args: None)

    with panel.app.test_client() as client:
        response = client.post("/i/demo/terminal/session")

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "id": "run-1"}
    assert calls == ["demo"]


def test_instance_terminal_input_rejects_other_users_session(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"id": 8, "username": "other"}, inst))
    monkeypatch.setattr(panel, "_model_setup_runs", {"run-1": {"owner_id": 7, "slug": "demo"}})

    with panel.app.test_client() as client:
        response = client.post("/i/demo/terminal/write/run-1", json={"input": "ls\r"})

    assert response.status_code == 404


def test_instance_terminal_input_accepts_valid_keys(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    inst = {"slug": "demo"}
    monkeypatch.setattr(panel, "_owned_instance", lambda slug: ({"id": 7, "username": "owner"}, inst))
    monkeypatch.setattr(panel, "_model_setup_runs", {"run-1": {"owner_id": 7, "slug": "demo"}})
    calls = []
    monkeypatch.setattr(panel.docker_ops, "write_instance_terminal", lambda run_id, data: calls.append((run_id, data)))

    with panel.app.test_client() as client:
        good = client.post("/i/demo/terminal/write/run-1", json={"input": "ls -la\r"})
        bad = client.post("/i/demo/terminal/write/run-1", json={"input": "x" * 10000})

    assert good.status_code == 200
    assert bad.status_code == 400
    assert calls == [("run-1", "ls -la\r")]


def test_instance_terminal_child_uses_xterm_environment(monkeypatch):
    recorded = {}

    class Process:
        def poll(self): return None

    monkeypatch.setattr(docker_ops, "ps_one", lambda *_: True)
    monkeypatch.setattr(docker_ops.pty, "openpty", lambda: (10, 11))
    monkeypatch.setattr(docker_ops.os, "close", lambda *_: None)
    monkeypatch.setattr(docker_ops.os, "set_blocking", lambda *_: None)
    monkeypatch.setattr(docker_ops.subprocess, "Popen", lambda argv, **kwargs: recorded.update(argv=argv, env=kwargs["env"]) or Process())

    run = docker_ops.start_instance_terminal("demo")

    assert run
    assert recorded["env"]["TERM"] == "xterm-256color"
    assert "/bin/bash" in recorded["argv"]
    assert "-w" in recorded["argv"]
    assert "/opt/data" in recorded["argv"]
