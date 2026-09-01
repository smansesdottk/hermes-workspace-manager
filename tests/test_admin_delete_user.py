#!/usr/bin/env python3
"""Regresi: (1) elemen [hidden] tak boleh dipaksa tampil oleh CSS ID-selector;
(2) fitur hapus user oleh admin — tuntas tanpa residu dan aman.

Self-contained: semua state di temp dir sendiri, Docker difake via monkeypatch.
"""
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as panel
import manager


@pytest.fixture()
def env(monkeypatch):
    d = tempfile.mkdtemp(prefix="dm-delusr-test-")
    monkeypatch.setattr(manager, "BASE_DIR", d)
    monkeypatch.setattr(manager, "INSTANCES_DIR", os.path.join(d, "instances"))
    monkeypatch.setattr(manager, "DB_PATH", os.path.join(d, "panel.db"))
    os.makedirs(manager.INSTANCES_DIR, exist_ok=True)
    manager.init_db()
    panel.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return d


@pytest.fixture()
def admin_client(env, monkeypatch):
    """Admin terlogin (root/password-admin) lewat alur form sungguhan."""
    manager.create_user("root", "password-admin", "admin")
    with panel.app.test_client() as c:
        page = c.get("/login").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        r = c.post("/login", data={"username": "root", "password": "password-admin",
                                   "csrf_token": token})
        assert r.status_code == 302
        yield c


def _fake_compose_ok(monkeypatch, calls):
    def fake_compose(d, *args, **kwargs):
        calls.append((d, args))
        return {"ok": True, "out": "", "err": ""}
    monkeypatch.setattr(manager.docker_ops, "compose", fake_compose)


def _make_instance(slug: str, owner_id: int) -> None:
    """Baris instance + folder compose tanpa menyentuh Docker sungguhan."""
    d = manager.instance_dir(slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "compose.yaml"), "w") as f:
        f.write("services: {}\n")
    with manager.db() as c:
        c.execute(
            """INSERT INTO instances(slug,owner_id,hermes_port,router_port,status,
               dashboard_password,router_password,auth_secret,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (slug, owner_id, 21001, 21002, "running", "x", "y", "z", manager.now()))


# ---------- QR: [hidden] harus menang atas selector ID ----------

def test_hidden_attr_tidak_dikalahkan_css(env, admin_client):
    page = admin_client.get("/dashboard").get_data(as_text=True)
    assert "[hidden]{display:none" in page.replace(" ", "")


# ---------- hapus user: level manager ----------

def test_delete_user_tuntas_tanpa_residu(env, monkeypatch):
    uid, _ = manager.create_user("budi", None, "user")
    _make_instance("budiweb", uid)
    calls: list = []
    _fake_compose_ok(monkeypatch, calls)

    result = manager.delete_user(uid)

    assert result == {"username": "budi", "removed_instances": ["budiweb"]}
    # compose down dipanggil dengan flag membersihkan volume & orphan
    assert any("down" in args and "-v" in args and "--remove-orphans" in args
               for _, args in calls)
    assert not os.path.exists(manager.instance_dir("budiweb"))  # folder data hilang
    with manager.db() as c:
        assert c.execute("SELECT COUNT(*) n FROM users WHERE id=?", (uid,)
                         ).fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) n FROM instances WHERE owner_id=?", (uid,)
                         ).fetchone()["n"] == 0


def test_delete_user_gagal_compose_meninggalkan_semua(env, monkeypatch):
    """Compose gagal -> user & instance TETAP ADA (tidak setengah jalan)."""
    uid, _ = manager.create_user("budi", None, "user")
    _make_instance("budiweb", uid)
    monkeypatch.setattr(manager.docker_ops, "compose",
                        lambda d, *a, **k: {"ok": False, "out": "", "err": "boom"})

    with pytest.raises(RuntimeError):
        manager.delete_user(uid)

    with manager.db() as c:
        assert c.execute("SELECT COUNT(*) n FROM users WHERE id=?", (uid,)
                         ).fetchone()["n"] == 1
        assert c.execute("SELECT COUNT(*) n FROM instances WHERE owner_id=?", (uid,)
                         ).fetchone()["n"] == 1


def test_delete_user_tak_ada(env, monkeypatch):
    calls: list = []
    _fake_compose_ok(monkeypatch, calls)
    with pytest.raises(ValueError):
        manager.delete_user(9999)


def test_delete_user_tolak_admin_aktif_terakhir(env, monkeypatch):
    """Satu-satunya admin aktif tidak boleh dihapus (level manager,
    tanpa tergantung sesi siapa yang sedang login)."""
    uid_boss, _ = manager.create_user("boss", None, "admin")
    calls: list = []
    _fake_compose_ok(monkeypatch, calls)
    with pytest.raises(ValueError, match="admin"):
        manager.delete_user(uid_boss)
    with manager.db() as c:
        assert c.execute("SELECT COUNT(*) n FROM users WHERE id=?", (uid_boss,)
                         ).fetchone()["n"] == 1


def test_delete_user_admin_nonaktif_boleh(env, monkeypatch):
    """Admin yang sudah NONAKTIF boleh dihapus walau tak ada admin aktif lain."""
    uid_boss, _ = manager.create_user("boss", None, "admin")
    manager.set_user_active(uid_boss, False)
    calls: list = []
    _fake_compose_ok(monkeypatch, calls)
    result = manager.delete_user(uid_boss)
    assert result["removed_instances"] == []


# ---------- hapus user: level route ----------

def _csrf_of(c) -> str:
    import re as _re
    page = c.get("/dashboard").get_data(as_text=True)
    return _re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


def test_route_tolak_hapus_akun_sendiri(env, admin_client):
    """Admin yang login tidak boleh menghapus akunnya sendiri via dashboard."""
    with manager.db() as c:
        uid = c.execute("SELECT id FROM users WHERE username='root'").fetchone()["id"]
    r = admin_client.post(f"/admin/users/{uid}/delete",
                          data={"csrf_token": _csrf_of(admin_client)},
                          follow_redirects=True)
    assert "akun sendiri" in r.get_data(as_text=True)
    with manager.db() as c:
        assert c.execute("SELECT COUNT(*) n FROM users WHERE id=?", (uid,)
                         ).fetchone()["n"] == 1


def test_route_hapus_user_sukses(env, admin_client, monkeypatch):
    uid, _ = manager.create_user("budi", None, "user")
    calls: list = []
    _fake_compose_ok(monkeypatch, calls)
    r = admin_client.post(f"/admin/users/{uid}/delete",
                          data={"csrf_token": _csrf_of(admin_client)},
                          follow_redirects=True)
    assert "dihapus" in r.get_data(as_text=True)
    with manager.db() as c:
        assert c.execute("SELECT COUNT(*) n FROM users WHERE id=?", (uid,)
                         ).fetchone()["n"] == 0
