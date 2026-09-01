#!/usr/bin/env python3
"""Security regression tests: CSRF and per-instance credentials."""
import os
import sys
import tempfile
import re

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as panel
import manager


@pytest.fixture()
def client(monkeypatch):
    d = tempfile.mkdtemp(prefix="dm-sec-test-")
    monkeypatch.setattr(manager, "BASE_DIR", d)
    monkeypatch.setattr(manager, "INSTANCES_DIR", os.path.join(d, "instances"))
    monkeypatch.setattr(manager, "DB_PATH", os.path.join(d, "panel.db"))
    os.makedirs(manager.INSTANCES_DIR, exist_ok=True)
    manager.init_db()
    owner, _ = manager.create_user("budi", "password-budi", "user")
    other, _ = manager.create_user("andi", "password-andi", "user")
    with manager.db() as c:
        c.execute("""INSERT INTO instances(slug,owner_id,hermes_port,router_port,status,
          dashboard_password,router_password,auth_secret,created_at)
          VALUES('budi',?,21000,21001,'running','hermes-rahasia','router-rahasia','x','now')""", (owner,))
    panel.app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=True)
    with panel.app.test_client() as c:
        yield c, other


def login(c, username, password):
    page = c.get('/login').get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    return c.post('/login', data={"username": username, "password": password, "csrf_token": token})


def csrf(c, path='/dashboard'):
    page = c.get(path, follow_redirects=True).get_data(as_text=True)
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', page).group(1)


def test_credentials_require_csrf_and_owner(client):
    c, _ = client
    login(c, 'budi', 'password-budi')
    assert c.post('/i/budi/credentials').status_code == 400
    r = c.post('/i/budi/credentials', data={'csrf_token': csrf(c)})
    assert r.status_code == 200
    assert r.get_json() == {"hermes_username": "admin", "hermes_password": "hermes-rahasia", "router_password": "router-rahasia"}


def test_other_user_cannot_reveal_instance_credentials(client):
    c, _ = client
    login(c, 'andi', 'password-andi')
    token = csrf(c, '/')
    assert c.post('/i/budi/credentials', data={'csrf_token': token}).status_code == 404


def test_all_state_changing_routes_reject_missing_csrf(client):
    c, _ = client
    login(c, 'budi', 'password-budi')
    assert c.post('/i/budi/action', data={'do': 'restart'}).status_code == 400
