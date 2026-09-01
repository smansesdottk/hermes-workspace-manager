#!/usr/bin/env python3
"""Regresi: security headers, rate limit login, validasi password instance.

Self-contained: semua state di temp dir sendiri, tanpa bergantung urutan test.
"""
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as panel
import manager


def _fresh_env(monkeypatch):
    d = tempfile.mkdtemp(prefix="dm-fix-test-")
    monkeypatch.setattr(manager, "BASE_DIR", d)
    monkeypatch.setattr(manager, "INSTANCES_DIR", os.path.join(d, "instances"))
    monkeypatch.setattr(manager, "DB_PATH", os.path.join(d, "panel.db"))
    os.makedirs(manager.INSTANCES_DIR, exist_ok=True)
    manager.init_db()
    return d


@pytest.fixture()
def client(monkeypatch):
    _fresh_env(monkeypatch)
    manager.create_user("budi", "password-budi", "user")
    panel.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    # _login_fails adalah state modul bersama: kosongkan agar lockout dari
    # test ini tidak bocor ke test lain dalam satu proses pytest.
    panel._login_fails.clear()
    with panel.app.test_client() as c:
        yield c
    panel._login_fails.clear()


@pytest.fixture()
def isolated_env(monkeypatch):
    return _fresh_env(monkeypatch)


def _csrf(c):
    page = c.get('/login').get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def test_security_headers_present(client):
    r = client.get('/login')
    assert r.headers.get('X-Frame-Options') == 'DENY'
    assert r.headers.get('X-Content-Type-Options') == 'nosniff'
    assert 'frame-ancestors' in (r.headers.get('Content-Security-Policy') or '')
    # same-origin (bukan no-referrer): flask-wtf SSL-strict menuntut header
    # Referer pada POST via HTTPS; no-referrer membuat browser tak bisa
    # mengirimnya -> login 400 "The referrer header is missing."
    assert r.headers.get('Referrer-Policy') == 'same-origin'


def test_login_via_https_proxy_tanpa_referer(client):
    """Regresi kondisi produksi: gunicorn memercayai X-Forwarded-Proto dari
    proxy loopback (Cloudflare tunnel), sehingga CSRF strict aktif. Login
    sah tanpa Referer tidak boleh ditolak 400."""
    page = client.get('/login')
    token = re.search(
        r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True)
    ).group(1)
    r = client.post(
        '/login',
        data={'username': 'budi', 'password': 'password-budi',
              'csrf_token': token},
        environ_overrides={'wsgi.url_scheme': 'https'})
    assert r.status_code != 400, r.get_data(as_text=True)[:200]
    assert r.status_code == 302  # login sukses -> redirect


def test_cookie_secure_on_https_path(client):
    r = client.get('/login', headers={'X-Forwarded-Proto': 'https'})
    assert 'Secure' in (r.headers.get('Set-Cookie') or '')


def test_cookie_not_secure_on_local_http(client):
    r = client.get('/login')
    assert 'Secure' not in (r.headers.get('Set-Cookie') or '')


def test_login_rate_limit_locks_after_max_fails(client):
    token = _csrf(client)
    for _ in range(panel.LOGIN_MAX_FAILS):
        client.post('/login', data={'username': 'budi',
                                    'password': 'salah', 'csrf_token': token})
    r = client.post('/login', data={'username': 'budi',
                                    'password': 'password-budi', 'csrf_token': token})
    # Password benar pun ditolak selama masa kunci.
    assert 'Coba lagi dalam' in r.get_data(as_text=True)
    with manager.db() as c:
        n = c.execute(
            "SELECT COUNT(*) n FROM audit WHERE action='login_failed'").fetchone()['n']
    assert n >= panel.LOGIN_MAX_FAILS


def test_create_instance_rejects_weak_dash_password(isolated_env):
    uid, _ = manager.create_user('pemilik', None, 'user')
    with pytest.raises(ValueError):
        manager.create_instance('kuat', uid, dash_password='123')


def test_next_ports_has_ceiling(monkeypatch):
    _fresh_env(monkeypatch)
    monkeypatch.setattr(manager, 'PORT_SCAN_CEILING', 21000)
    # Semua port pada ceiling dianggap terpakai -> harus raise, bukan loop selamanya.
    monkeypatch.setattr(manager, '_port_free', lambda port: False)
    with pytest.raises(RuntimeError):
        manager.next_ports()
