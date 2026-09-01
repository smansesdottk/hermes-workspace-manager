import docker_ops


def test_ps_one_uses_exact_container_name(monkeypatch):
    calls = []
    monkeypatch.setattr(docker_ops, "_run", lambda argv, **_: calls.append(argv) or {"ok": True, "out": "true\n"})

    assert docker_ops.ps_one("budi", "hermes") is True
    assert calls[0] == [docker_ops.DOCKER_BIN, "inspect", "-f", "{{.State.Running}}", "dockerman-budi-hermes"]


def test_ps_one_returns_false_for_missing_container(monkeypatch):
    monkeypatch.setattr(docker_ops, "_run", lambda *_, **__: {"out": "", "ok": False})
    assert docker_ops.ps_one("budi", "hermes") is False
