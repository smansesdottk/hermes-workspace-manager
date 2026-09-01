import pytest
from datetime import datetime, timedelta, timezone
import manager
import app as panel


def test_expiry_calculation_and_status():
    # 1. Lifetime
    exp_life = manager.calculate_expiry("lifetime")
    assert exp_life == "lifetime"
    info_life = manager.get_expiry_info(exp_life)
    assert info_life["is_expired"] is False
    assert info_life["is_lifetime"] is True
    
    # 2. 1, 2, 3 Days test
    exp_1d = manager.calculate_expiry("1d")
    info_1d = manager.get_expiry_info(exp_1d)
    assert info_1d["is_expired"] is False
    assert info_1d["warning"] is True  # 1d <= 3 days warning
    
    exp_7d = manager.calculate_expiry("7d")
    info_7d = manager.get_expiry_info(exp_7d)
    assert info_7d["is_expired"] is False
    assert info_7d["days_left"] in (6, 7)
    
    # 3. Direct Expired / Locked option
    exp_locked = manager.calculate_expiry("expired")
    info_locked = manager.get_expiry_info(exp_locked)
    assert info_locked["is_expired"] is True
    assert manager.is_instance_expired(exp_locked) is True
    
    # 4. Warning (<= 3 days)
    near_dt = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    info_near = manager.get_expiry_info(near_dt)
    assert info_near["warning"] is True
    assert info_near["is_expired"] is False
    
    # 5. Expired
    past_dt = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    info_past = manager.get_expiry_info(past_dt)
    assert info_past["is_expired"] is True
    assert manager.is_instance_expired(past_dt) is True



def test_user_cannot_access_expired_instance(monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    past_dt = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    inst = {"slug": "expired-slug", "owner_id": 2, "expires_at": past_dt, "status": "stopped"}
    
    monkeypatch.setattr(panel.manager, "get_instance_for_user", lambda slug, uid, is_admin: inst)
    monkeypatch.setattr(panel, "current_user", lambda: {"id": 2, "username": "budi", "role": "user"})
    
    with panel.app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user"] = {"id": 2, "username": "budi", "role": "user"}
        res = client.get("/i/expired-slug")
        # Harusnya diarahkan kembali ke dashboard (redirect 302)
        assert res.status_code == 302
