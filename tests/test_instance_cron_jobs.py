import os
import json
import pytest
import manager
import app as panel


def _setup_instance_with_cron(tmp_path, monkeypatch):
    panel.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    monkeypatch.setattr(manager, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(manager, "INSTANCES_DIR", str(tmp_path / "instances"))
    monkeypatch.setattr(manager, "DB_PATH", str(tmp_path / "panel.db"))
    manager.init_db()

    uid, _ = manager.create_user("budi", "password123", "user")
    slug = "cron-test"
    cron_dir = tmp_path / "instances" / slug / "hermes-data" / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)

    jobs_data = {
        "jobs": [
            {
                "id": "job_1",
                "name": "Kirim Berita Pagi",
                "schedule": "0 8 * * *",
                "schedule_display": "Setiap hari jam 08:00",
                "prompt": "Rangkum berita AI terkini",
                "state": "scheduled",
                "enabled": True,
                "deliver": "telegram:12345",
                "next_run_at": "2026-08-27T08:00:00+08:00",
                "last_run_at": "2026-08-26T08:00:00+08:00",
                "last_status": "success"
            },
            {
                "id": "job_2",
                "name": "Backup Database Mingguan",
                "schedule": "0 0 * * 0",
                "schedule_display": "Mingguan (Minggu 00:00)",
                "prompt": "Backup database sekolah",
                "state": "paused",
                "enabled": False,
                "deliver": "local",
                "next_run_at": None,
                "last_run_at": None,
                "last_status": None
            }
        ],
        "updated_at": "2026-08-26T20:00:00+08:00"
    }

    with open(cron_dir / "jobs.json", "w") as f:
        json.dump(jobs_data, f)

    return uid, slug, cron_dir


def test_list_cron_jobs(tmp_path, monkeypatch):
    uid, slug, cron_dir = _setup_instance_with_cron(tmp_path, monkeypatch)

    jobs = manager.list_instance_cron_jobs(slug)
    assert len(jobs) == 2
    assert jobs[0]["name"] == "Kirim Berita Pagi"
    assert jobs[0]["enabled"] is True
    assert jobs[1]["name"] == "Backup Database Mingguan"
    assert jobs[1]["state"] == "paused"


def test_cron_job_toggle_and_delete(tmp_path, monkeypatch):
    uid, slug, cron_dir = _setup_instance_with_cron(tmp_path, monkeypatch)

    # 1. Pause job 1
    res = manager.toggle_instance_cron_job(slug, "job_1", enable=False)
    assert res["ok"] is True
    jobs = manager.list_instance_cron_jobs(slug)
    j1 = next(j for j in jobs if j["id"] == "job_1")
    assert j1["enabled"] is False
    assert j1["state"] == "paused"

    # 2. Resume job 1
    res2 = manager.toggle_instance_cron_job(slug, "job_1", enable=True)
    assert res2["ok"] is True
    jobs = manager.list_instance_cron_jobs(slug)
    j1 = next(j for j in jobs if j["id"] == "job_1")
    assert j1["enabled"] is True
    assert j1["state"] == "scheduled"

    # 3. Delete job 2
    res_del = manager.delete_instance_cron_job(slug, "job_2")
    assert res_del["ok"] is True
    jobs_after = manager.list_instance_cron_jobs(slug)
    assert len(jobs_after) == 1
    assert jobs_after[0]["id"] == "job_1"


def test_cron_create_job(tmp_path, monkeypatch):
    uid, slug, cron_dir = _setup_instance_with_cron(tmp_path, monkeypatch)

    res = manager.create_instance_cron_job(
        slug=slug,
        name="Pengingat Rapat Harian",
        schedule="0 14 * * 1-5",
        prompt="Ingatkan staf TU untuk rapat koordinasi",
        deliver="whatsapp"
    )
    assert res["ok"] is True

    jobs = manager.list_instance_cron_jobs(slug)
    assert len(jobs) == 3
    new_job = next(j for j in jobs if j["name"] == "Pengingat Rapat Harian")
    assert new_job["schedule"] == "0 14 * * 1-5"
    assert new_job["deliver"] == "whatsapp"
