import io
import os
import tarfile
import pytest
from flask import url_for
import app as panel
import manager


def _csrf(c, path="/dashboard"):
    page = c.get(path, follow_redirects=True).get_data(as_text=True)
    import re
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', page)
    if m:
        return m.group(1)
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    return m.group(1) if m else ""


def test_restore_blocks_symlinks_and_path_traversal(tmp_path, monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    monkeypatch.setattr(manager, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    manager.init_db()

    uid, _ = manager.create_user("budi", "password123", "user")
    slug = "test-sec"
    target_data = tmp_path / "instances" / slug / "hermes-data"
    target_data.mkdir(parents=True, exist_ok=True)

    # File sensitif di luar folder instance yang tidak boleh tertimpa
    victim_file = tmp_path / "important_host_secret.txt"
    victim_file.write_text("ORIGINAL_SECRET")

    # Buat arsip tar jahat yang memuat symlink dan path traversal
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # 1. File legal
        legal_content = b"clean_data"
        t_legal = tarfile.TarInfo("test-sec-hermes-data/clean.txt")
        t_legal.size = len(legal_content)
        tar.addfile(t_legal, io.BytesIO(legal_content))

        # 2. Symlink jahat yang mengarah ke victim_file di host
        t_sym = tarfile.TarInfo("test-sec-hermes-data/evil_symlink.txt")
        t_sym.type = tarfile.SYMTYPE
        t_sym.linkname = str(victim_file)
        tar.addfile(t_sym)

    buf.seek(0)

    monkeypatch.setattr(panel.manager, "get_instance_for_user", lambda s, u, is_adm: {"slug": slug, "owner_id": uid, "status": "running"})
    monkeypatch.setattr(panel.manager, "instance_action", lambda s, a: {"ok": True})
    monkeypatch.setattr(panel, "current_user", lambda: {"id": uid, "username": "budi", "role": "user"})

    with panel.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = {"id": uid, "username": "budi", "role": "user"}

        res = client.post(
            f"/i/{slug}/restore",
            data={"backup_file": (buf, "backup.tar.gz")},
            content_type="multipart/form-data"
        )
        assert res.status_code == 302

    # File legal harus diekstrak
    assert (target_data / "clean.txt").read_text() == "clean_data"
    # File sensitif di host tidak boleh berubah
    assert victim_file.read_text() == "ORIGINAL_SECRET"
    # Symlink jahat tidak boleh terbentuk di dalam target_data
    assert not (target_data / "evil_symlink.txt").is_symlink()


def test_rate_limiting_on_chat(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    panel._chat_rate_limits.clear()

    monkeypatch.setattr(panel.manager, "test_chat_instance", lambda s, m: {"ok": True, "reply": "ok"})
    monkeypatch.setattr(panel, "_owned_instance", lambda s: ({"id": 1, "username": "budi", "role": "user"}, {"slug": "demo"}))

    with panel.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = {"id": 1, "username": "budi", "role": "user"}

        # Kirim 30 chat berturut-turut (masih aman)
        for _ in range(30):
            r = client.post("/i/demo/model/chat", json={"message": "Halo"})
            assert r.status_code == 200

        # Request ke-31 harus terkena rate limit 429 Too Many Requests
        r_blocked = client.post("/i/demo/model/chat", json={"message": "Spam"})
        assert r_blocked.status_code == 429
        assert "terlalu cepat" in r_blocked.get_json()["error"]
