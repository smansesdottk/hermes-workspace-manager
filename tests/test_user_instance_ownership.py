import manager


def test_list_users_exposes_each_owned_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    manager.init_db()
    user_id, _ = manager.create_user("owner", "SafePassword123", "user")

    with manager.db() as conn:
        conn.executemany(
            """INSERT INTO instances(slug, owner_id, hermes_port, router_port, status,
                 dashboard_password, router_password, auth_secret, created_at, expires_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            [
                ("owner-alpha", user_id, 21100, 21101, "running", "x", "y", "z", manager.now(), "lifetime"),
                ("owner-beta", user_id, 21102, 21103, "stopped", "x", "y", "z", manager.now(), "lifetime"),
            ],
        )

    owner = next(row for row in manager.list_users() if row["id"] == user_id)
    assert owner["instance_count"] == 2
    assert [item["slug"] for item in owner["instances_owned"]] == ["owner-alpha", "owner-beta"]
    assert owner["instances_owned"][0]["status"] == "running"
    assert owner["instances_owned"][1]["status"] == "stopped"
    assert "owner-alpha" in owner["slugs"]
    assert "owner-beta" in owner["slugs"]
