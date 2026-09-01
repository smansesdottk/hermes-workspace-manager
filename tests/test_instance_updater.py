import manager


def test_recreate_instance_containers(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    manager.init_db()

    uid, _ = manager.create_user("budi", "password123", "user")
    slug = "inst-update"
    inst_dir = tmp_path / "instances" / slug
    inst_dir.mkdir(parents=True, exist_ok=True)
    compose_file = inst_dir / "compose.yaml"
    compose_file.write_text("services:\n  hermes:\n    image: test\n")

    with manager.db() as conn:
        conn.execute(
            """INSERT INTO instances(slug, owner_id, hermes_port, router_port, status,
                 dashboard_password, router_password, auth_secret, created_at, expires_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (slug, uid, 21200, 21201, "running", "p1", "p2", "sec", manager.now(), "lifetime")
        )

    calls = []
    def fake_compose(d, *args, **kw):
        calls.append(args)
        return {"ok": True, "out": "", "err": ""}

    monkeypatch.setattr(manager.docker_ops, "compose", fake_compose)
    monkeypatch.setattr(manager.docker_ops, "ps_one", lambda *_: True)

    res = manager.update_instance_containers(slug)
    assert res["ok"] is True
    # Harus menjalankan down lalu up -d untuk menerapkan image baru
    assert any("up" in c for c in calls)


def test_update_all_instances(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    manager.init_db()

    uid, _ = manager.create_user("budi", "password123", "user")
    for s in ("inst1", "inst2"):
        d = tmp_path / "instances" / s
        d.mkdir(parents=True, exist_ok=True)
        (d / "compose.yaml").write_text("services:\n")
        with manager.db() as conn:
            conn.execute(
                """INSERT INTO instances(slug, owner_id, hermes_port, router_port, status,
                     dashboard_password, router_password, auth_secret, created_at, expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (s, uid, 21200, 21201, "running", "p1", "p2", "sec", manager.now(), "lifetime")
            )

    monkeypatch.setattr(manager.docker_ops, "compose", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(manager.docker_ops, "ps_one", lambda *_: True)

    res = manager.update_all_instances_containers()
    assert res["total"] == 2
    assert len(res["success"]) == 2
