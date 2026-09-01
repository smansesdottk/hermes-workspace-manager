import manager


def test_get_version_info(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    manager.init_db()

    uid, _ = manager.create_user("budi", "password123", "user")
    slug = "v-test"
    inst_dir = tmp_path / "instances" / slug
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "compose.yaml").write_text("services:\n")

    with manager.db() as conn:
        conn.execute(
            """INSERT INTO instances(slug, owner_id, hermes_port, router_port, status,
                 dashboard_password, router_password, auth_secret, created_at, expires_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (slug, uid, 21200, 21201, "running", "p1", "p2", "sec", manager.now(), "lifetime")
        )

    # Mock docker inspect
    def fake_inspect(target, is_container=False):
        if target == "local/hermes-agent-dockerman:latest":
            return "sha256:latest_seed_456 2026-08-30T00:00:00Z"
        return "sha256:old_img_123 2026-08-20T00:00:00Z"

    monkeypatch.setattr(manager, "_inspect_image_id", fake_inspect)

    info = manager.get_instance_version_info(slug)
    assert info["has_update"] is True
    assert "old_img" in info["instance_image"]
    assert "latest_seed" in info["seed_image"]


def test_async_seed_rebuild_status():
    status = manager.get_seed_build_status()
    assert "running" in status
    assert "last_build" in status
