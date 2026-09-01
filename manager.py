#!/usr/bin/env python3
"""manager.py — logika bisnis: DB user/instance, render compose, lifecycle.

Semua operasi Docker melewati docker_ops.py (allowlist). Tidak ada shell.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import requests
from flask import current_app

import docker_ops

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCES_DIR = os.path.join(BASE_DIR, "instances")
DB_PATH = os.path.join(BASE_DIR, "panel.db")
# Prioritaskan image lokal jika ada, atau fallback ke official image di Docker Hub
def _resolve_default_hermes_image() -> str:
    env_img = os.environ.get("DM_HERMES_IMAGE")
    if env_img:
        return env_img
    # Jika image local/hermes-agent-dockerman:latest sudah ter-build di mesin ini
    if docker_ops.has_image("local/hermes-agent-dockerman:latest"):
        return "local/hermes-agent-dockerman:latest"
    # Fallback ke image resmi Docker Hub publik (siap di-pull di Windows/Linux mana pun)
    return "nousresearch/hermes-agent:latest"

def _resolve_default_router_image() -> str:
    return os.environ.get("DM_ROUTER_IMAGE", "decolua/9router:latest")

HERMES_IMAGE = _resolve_default_hermes_image()
ROUTER_IMAGE = _resolve_default_router_image()

DEFAULT_LIMITS = ("1.00", "1500m", "8G")  # CPU, RAM, writable layer
# Batas atas pemindaian port berpasangan (hermes, router).
PORT_SCAN_CEILING = 29998

COMPOSE_TEMPLATE = """services:
  hermes:
    image: {hermes_image}
    container_name: dockerman-{slug}-hermes
    restart: unless-stopped
    ports:
      - "127.0.0.1:{hermes_port}:9119"
    volumes:
      - ./hermes-data:/opt/data
    environment:
      HERMES_UID: "1000"
      HERMES_GID: "1000"
      HERMES_DASHBOARD: "true"
      HERMES_DASHBOARD_BASIC_AUTH_USERNAME: admin
      HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: "{dash_password}"
      HERMES_DASHBOARD_BASIC_AUTH_SECRET: "{auth_secret}"
    command: ["gateway", "run"]
    cpus: '{cpu_limit}'
    mem_limit: {memory_limit}
    pids_limit: 256
    networks: [n_{slug}]

  9router:
    image: {router_image}
    container_name: dockerman-{slug}-9router
    restart: unless-stopped
    ports:
      - "127.0.0.1:{router_port}:20128"
    volumes:
      - ./9router-data:/app/data
    environment:
      DATA_DIR: /app/data
      PORT: "20128"
      HOSTNAME: "0.0.0.0"
      NODE_ENV: production
      INITIAL_PASSWORD: "{router_password}"
    mem_limit: 700m
    pids_limit: 128
    networks: [n_{slug}]

networks:
  n_{slug}:
    name: dm-net-{slug}
"""


def db() -> sqlite3.Connection:
    # DB memuat password layanan; pastikan owner-only
    if os.path.exists(DB_PATH):
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
    except Exception:
        pass
    return conn



def init_db():
    with db() as c:
        c.executescript(
            """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','user')),
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instances(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  owner_id INTEGER NOT NULL REFERENCES users(id),
  hermes_port INTEGER NOT NULL,
  router_port INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'created',
  dashboard_password TEXT NOT NULL,
  router_password TEXT NOT NULL,
  auth_secret TEXT NOT NULL,
  created_at TEXT NOT NULL,
  hermes_url TEXT NOT NULL DEFAULT '',
  router_url TEXT NOT NULL DEFAULT '',
  cpu_limit TEXT NOT NULL DEFAULT '1.00',
  memory_limit TEXT NOT NULL DEFAULT '1500m',
  storage_limit TEXT NOT NULL DEFAULT '8G',
  expires_at TEXT NOT NULL DEFAULT 'lifetime'
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS pricing_plans(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  badge TEXT NOT NULL DEFAULT '',
  original_price INTEGER NOT NULL,
  promo_price INTEGER NOT NULL,
  period TEXT NOT NULL DEFAULT '/bulan',
  stock INTEGER NOT NULL DEFAULT 10,
  is_active INTEGER NOT NULL DEFAULT 1,
  features_json TEXT NOT NULL DEFAULT '[]',
  order_num INTEGER NOT NULL DEFAULT 0
);
"""
        )
        # Inisialisasi default 3 paket jika tabel kosong
        plan_count = c.execute("SELECT COUNT(*) as count FROM pricing_plans").fetchone()["count"]
        if plan_count == 0:
            import json
            default_plans = [
                (
                    "starter",
                    "Starter Community",
                    "Paling Hemat",
                    100000,
                    50000,
                    "/bulan",
                    15,
                    1,
                    json.dumps([
                        "1 Dedicated Instance Hermes + 9Router pribadi",
                        "Integrasi WhatsApp (Self-Chat / Bot) & Telegram Bot",
                        "Provider Bawaan Gratis: OpenCode, Muse-Spark, HY3-Free, Qwen-Free",
                        "Bebas pasang API Key pribadi / OAuth (OpenAI, Claude, DeepSeek, Gemini, dll) untuk kecerdasan maksimal tanpa batas",
                        "Live Web Test Chat + Memory Multi-Turn Conversation",
                        "Editor Persona SOUL.md & 1-Klik Backup/Restore Data",
                        "Alokasi Resource: 1.0 CPU · 1.5 GB RAM · Kuota 8 GB"
                    ]),
                    1
                ),
                (
                    "pro_gemini",
                    "Pro Multimodel (Gemini & Qwen)",
                    "Populer & Cerdas",
                    300000,
                    150000,
                    "/bulan",
                    8,
                    1,
                    json.dumps([
                        "Semua fitur & kapabilitas di Paket Starter Community",
                        "Akses Terintegrasi Model API Google Gemini & Qwen (Latest Release)",
                        "Bebas hubungkan API Key mandiri / OAuth provider premium tambahan kapan saja",
                        "Konteks Penalaran Panjang & Analisis Dokumen Multi-Format",
                        "Riset Web Real-time Cepat & Otomasi Jadwal Tugas Harian",
                        "Prioritas Jalur 9Router Gateway Tanpa Antrean",
                        "Alokasi Resource Dioptimasi: 1.5 CPU · 2.0 GB RAM"
                    ]),
                    2
                ),
                (
                    "ultimate_claude",
                    "Ultimate Claude Power",
                    "Kekuatan Penuh (Coding & Agent)",
                    800000,
                    400000,
                    "/bulan",
                    5,
                    1,
                    json.dumps([
                        "Semua fitur & kapabilitas di Paket Pro Multimodel",
                        "Akses Penuh Model Unggulan Anthropic Claude (Latest Release)",
                        "Dukungan Penuh Custom API / OAuth Multi-Provider berbayar dengan fleksibilitas tertinggi",
                        "Kemampuan Eksekusi Kode Terminal Linux & Debugging Kompleks",
                        "Otonom Multi-Step Agentic Tool Use (Web, Terminal, Code, Files)",
                        "Cron Job Otomatis & Pemantauan Sistem 24/7 Terjadwal",
                        "Alokasi Resource Maksimal: 2.0 CPU · 3.0 GB RAM · Priority Support"
                    ]),
                    3
                )
            ]
            c.executemany(
                """INSERT INTO pricing_plans(id, name, badge, original_price, promo_price, period, stock, is_active, features_json, order_num)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                default_plans
            )


        # Buat akun admin default jika database baru pertama kali diinisialisasi
        user_count = c.execute("SELECT COUNT(*) as count FROM users").fetchone()["count"]
        if user_count == 0:
            from werkzeug.security import generate_password_hash
            default_admin = "admin"
            default_pass = "admin123"
            h = generate_password_hash(default_pass)
            c.execute(
                "INSERT INTO users (username, password_hash, role, is_active, created_at) VALUES (?, ?, 'admin', 1, ?)",
                (default_admin, h, now())
            )
            print(f"\n========================================================")
            print(f" [INFO] Akun Administrator Pertama Dibuat:")
            print(f"        Username : {default_admin}")
            print(f"        Password : {default_pass}")
            print(f"        (Ganti password Anda setelah login di menu Akun)")
            print(f"========================================================\n")

        # Migrasi ringan untuk database yang dibuat sebelum kolom tunnel/plan ada.

        columns = {r["name"] for r in c.execute("PRAGMA table_info(instances)")}
        for name, default in (("hermes_url", "''"), ("router_url", "''"),
                              ("cpu_limit", "'1.00'"), ("memory_limit", "'1500m'"),
                              ("storage_limit", "'8G'"), ("expires_at", "'lifetime'")):
            if name not in columns:
                c.execute(f"ALTER TABLE instances ADD COLUMN {name} TEXT NOT NULL DEFAULT {default}")



def calculate_expiry(plan: str, custom_days: int = 0) -> str:
    """Hitung tanggal kedaluwarsa ISO UTC berdasarkan paket berlangganan."""
    plan = str(plan or "lifetime").strip().lower()
    if plan in ("lifetime", "forever", "unlimited"):
        return "lifetime"
        
    now_dt = datetime.now(timezone.utc)
    if plan in ("expired", "lock", "locked"):
        # Langsung kedaluwarsa (1 hari lalu)
        return (now_dt - timedelta(days=1)).isoformat()
    elif plan == "1d":
        exp = now_dt + timedelta(days=1)
    elif plan == "2d":
        exp = now_dt + timedelta(days=2)
    elif plan == "3d":
        exp = now_dt + timedelta(days=3)
    elif plan == "7d":
        exp = now_dt + timedelta(days=7)
    elif plan in ("30d", "1m"):
        exp = now_dt + timedelta(days=30)
    elif plan == "3m":
        exp = now_dt + timedelta(days=90)
    elif plan == "6m":
        exp = now_dt + timedelta(days=180)
    elif plan == "1y":
        exp = now_dt + timedelta(days=365)
    elif plan == "custom" and custom_days > 0:
        exp = now_dt + timedelta(days=custom_days)
    elif plan.endswith("m") and plan[:-1].isdigit():
        months = int(plan[:-1])
        exp = now_dt + timedelta(days=months * 30)
    elif plan.endswith("d") and plan[:-1].isdigit():
        days = int(plan[:-1])
        exp = now_dt + timedelta(days=days)
    else:
        return "lifetime"
    return exp.isoformat()



def is_instance_expired(expires_at: str) -> bool:
    """Cek apakah masa berlaku instance sudah habis."""
    if not expires_at or expires_at == "lifetime":
        return False
    try:
        exp_dt = datetime.fromisoformat(expires_at)
        now_dt = datetime.now(timezone.utc)
        return now_dt > exp_dt
    except Exception:
        return False


def get_expiry_info(expires_at: str) -> dict:
    """Ambil informasi status kedaluwarsa, sisa hari, dan flag peringatan (3 hari sebelum habis)."""
    if not expires_at or expires_at == "lifetime":
        return {"is_expired": False, "is_lifetime": True, "label": "Lifetime (Selamanya)", "warning": False, "days_left": 9999}
    try:
        exp_dt = datetime.fromisoformat(expires_at)
        now_dt = datetime.now(timezone.utc)
        delta = exp_dt - now_dt
        days_left = delta.days
        hours_left = int(delta.total_seconds() // 3600)
        
        if delta.total_seconds() <= 0:
            return {
                "is_expired": True, "is_lifetime": False,
                "label": "Kedaluwarsa", "warning": True, "days_left": 0,
                "exp_date": exp_dt.strftime("%d %b %Y %H:%M UTC")
            }
            
        warning = days_left <= 3
        if days_left > 0:
            label = f"Sisa {days_left} hari ({exp_dt.strftime('%d %b %Y')})"
        else:
            label = f"Sisa {hours_left} jam ({exp_dt.strftime('%d %b %Y %H:%M')})"
            
        return {
            "is_expired": False, "is_lifetime": False,
            "label": label, "warning": warning, "days_left": days_left,
            "exp_date": exp_dt.strftime("%d %b %Y")
        }
    except Exception:
        return {"is_expired": False, "is_lifetime": True, "label": "Lifetime", "warning": False, "days_left": 9999}



# Zona Waktu Makassar (WITA, UTC+8)
WITA = timezone(timedelta(hours=8), name="WITA")


def now() -> str:
    return datetime.now(WITA).isoformat(timespec="seconds")



def audit(actor: str, action: str, detail: str = ""):
    with db() as c:
        c.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
                  (now(), actor, action, detail[:500]))


# ---------- users ----------

def create_user(username: str, password: str | None, role: str) -> tuple[int, str]:
    from werkzeug.security import generate_password_hash
    if role not in ("admin", "user"):
        raise ValueError("role tidak valid")
    username = username.strip().lower()
    if not re.match(r"^[a-z][a-z0-9_.-]{1,31}$", username):
        raise ValueError("username tidak valid (a-z, angka, titik, garis)")
    password = password or secrets.token_urlsafe(10)
    if len(password) < 8:
        raise ValueError("password minimal 8 karakter")
    h = generate_password_hash(password)
    with db() as c:
        cur = c.execute(
            "INSERT INTO users(username,password_hash,role,is_active,created_at) VALUES(?,?,?,1,?)",
            (username, h, role, now()))
        return cur.lastrowid, password


def set_user_active(user_id: int, active: bool):
    with db() as c:
        c.execute("UPDATE users SET is_active=? WHERE id=?", (1 if active else 0, user_id))


def set_password(user_id: int, password: str) -> None:
    from werkzeug.security import generate_password_hash
    if len(password) < 8:
        raise ValueError("password minimal 8 karakter")
    with db() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(password), user_id))


def reset_password(user_id: int) -> str:
    pw = secrets.token_urlsafe(10)
    set_password(user_id, pw)
    return pw


def list_users() -> list[dict]:
    """Daftar satu baris per akun, lengkap dengan instance miliknya untuk UI admin aman."""
    with db() as c:
        rows = [dict(r) for r in c.execute(
            """SELECT u.id, u.username, u.role, u.is_active, u.created_at,
                      GROUP_CONCAT(i.slug, ', ') as slugs,
                      COUNT(i.id) as instance_count
               FROM users u
               LEFT JOIN instances i ON i.owner_id=u.id
               GROUP BY u.id
               ORDER BY u.id"""
        ).fetchall()]
        owned = c.execute(
            "SELECT owner_id, slug, status, expires_at FROM instances ORDER BY owner_id, id"
        ).fetchall()

    by_owner: dict[int, list[dict]] = {}
    for instance in owned:
        item = dict(instance)
        item["expiry"] = get_expiry_info(item.get("expires_at", "lifetime"))
        by_owner.setdefault(item["owner_id"], []).append(item)
    for user in rows:
        user["instances_owned"] = by_owner.get(user["id"], [])
    return rows



def get_user(username: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username.lower(),)).fetchone()
    return dict(r) if r else None


def delete_user(uid: int) -> dict:
    """Hapus user secara TUNTAS dan aman:
    1. Tolak jika user tak ada, admin aktif terakhir, atau masih punya instance
       yang gagal dibersihkan dari Docker.
    2. Untuk tiap instance: compose down -v --remove-orphans (kontainer+jaringan+
       volume), hapus folder data, hapus baris DB.
    3. Terakhir hapus baris user. Gagal di tengah = tidak ada perubahan DB
       (urutan: Docker -> file -> DB), jadi tidak ada user yatim tanpa data.
    """
    with db() as c:
        row = c.execute("SELECT id,username,role,is_active FROM users WHERE id=?",
                        (uid,)).fetchone()
        if not row:
            raise ValueError("user tidak ditemukan")
        if row["role"] == "admin" and row["is_active"]:
            aktif = c.execute(
                "SELECT COUNT(*) n FROM users WHERE role='admin' AND is_active=1"
            ).fetchone()["n"]
            if aktif <= 1:
                raise ValueError("tidak bisa menghapus admin terakhir yang aktif")
        owned = [dict(r) for r in c.execute(
            "SELECT slug FROM instances WHERE owner_id=?", (uid,)).fetchall()]

    removed: list[str] = []
    for inst in owned:
        destroy_instance(inst["slug"], wipe_data=True)
        removed.append(inst["slug"])

    with db() as c:
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return {"username": row["username"], "removed_instances": removed}


# ---------- cron jobs management ----------

def _cron_jobs_file(slug: str) -> str:
    return os.path.join(instance_dir(slug), "hermes-data", "cron", "jobs.json")


def _cron_executions_db(slug: str) -> str:
    return os.path.join(instance_dir(slug), "hermes-data", "cron", "executions.db")


def list_instance_cron_jobs(slug: str) -> list[dict]:
    """Membaca daftar tugas terjadwal (cron jobs) milik instance."""
    path = _cron_jobs_file(slug)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            jobs = data.get("jobs", [])
            
        # Perkaya dengan info eksekusi terbaru jika executions.db ada
        db_path = _cron_executions_db(slug)
        if os.path.exists(db_path) and jobs:
            try:
                conn = sqlite3.connect(db_path, timeout=5.0)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                for j in jobs:
                    jid = j.get("id")
                    if jid:
                        last = c.execute(
                            "SELECT status, started_at, finished_at, error FROM executions WHERE job_id=? ORDER BY claimed_at DESC LIMIT 1",
                            (jid,)
                        ).fetchone()
                        if last:
                            j["last_exec_status"] = last["status"]
                            j["last_exec_time"] = last["finished_at"] or last["started_at"]
                            j["last_exec_error"] = last["error"]
                conn.close()
            except Exception:
                pass
        return jobs
    except Exception:
        return []


def toggle_instance_cron_job(slug: str, job_id: str, enable: bool) -> dict:
    """Mengaktifkan (resume) atau menonaktifkan (pause) tugas terjadwal."""
    path = _cron_jobs_file(slug)
    if not os.path.exists(path):
        return {"ok": False, "error": "File konfigurasi cron tidak ditemukan"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        jobs = data.get("jobs", [])
        target = None
        for j in jobs:
            if j.get("id") == job_id:
                j["enabled"] = bool(enable)
                j["state"] = "scheduled" if enable else "paused"
                target = j
                break
        if not target:
            return {"ok": False, "error": f"Job '{job_id}' tidak ditemukan"}
        data["updated_at"] = now()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return {"ok": True, "job": target}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def delete_instance_cron_job(slug: str, job_id: str) -> dict:
    """Menghapus tugas terjadwal dari instance."""
    path = _cron_jobs_file(slug)
    if not os.path.exists(path):
        return {"ok": False, "error": "File konfigurasi cron tidak ditemukan"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        jobs = data.get("jobs", [])
        orig_len = len(jobs)
        jobs = [j for j in jobs if j.get("id") != job_id]
        if len(jobs) == orig_len:
            return {"ok": False, "error": f"Job '{job_id}' tidak ditemukan"}
        data["jobs"] = jobs
        data["updated_at"] = now()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create_instance_cron_job(slug: str, name: str, schedule: str, prompt: str, deliver: str = "origin") -> dict:
    """Membuat tugas terjadwal baru di dalam instance."""
    cron_dir = os.path.join(instance_dir(slug), "hermes-data", "cron")
    os.makedirs(cron_dir, exist_ok=True)
    path = _cron_jobs_file(slug)
    data = {"jobs": [], "updated_at": now()}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass

    import uuid
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    new_job = {
        "id": job_id,
        "name": name.strip() or f"Tugas {job_id}",
        "schedule": schedule.strip(),
        "schedule_display": schedule.strip(),
        "prompt": prompt.strip(),
        "state": "scheduled",
        "enabled": True,
        "deliver": deliver.strip() or "origin",
        "created_at": now(),
        "next_run_at": None,
        "last_run_at": None,
        "last_status": None,
    }
    data.setdefault("jobs", []).append(new_job)
    data["updated_at"] = now()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return {"ok": True, "job": new_job}


def used_ports() -> set[int]:
    with db() as c:
        rows = c.execute("SELECT hermes_port, router_port FROM instances").fetchall()
    # port panel & host yang sudah terpakai di luar panel juga dihormati via reserved list
    reserved = set(range(5000, 5010)) | {20128, 20129, 9119, 9120}
    ports = reserved
    for r in rows:
        ports |= {r["hermes_port"], r["router_port"]}
    return ports


def _port_free(port: int) -> bool:
    """Cek port benar-benar bebas di host (tidak cukup cek DB saja)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def next_ports(count: int = 2) -> tuple[int, int]:
    """Cari dua port bebas mulai 21000 (cek DB + bind host).

    Dipindai sampai PORT_SCAN_CEILING; melebihi itu dianggap host penuh
    (bukan loop tanpa batas)."""
    used = used_ports()
    base = 21000
    while base <= PORT_SCAN_CEILING:
        if base not in used and base + 1 not in used and _port_free(base) and _port_free(base + 1):
            return base, base + 1
        base += 2
    raise RuntimeError("tidak ada pasangan port bebas (rentas 21000 habis)")


TUNNEL_RE = re.compile(r"^https://[a-z0-9-]+\.trycloudflare\.com$")


def validate_resource_limits(cpu: str, memory: str, storage: str) -> tuple[str, str, str]:
    """Allow a small, safe admin-controlled resource envelope."""
    try:
        cpu_value = float(cpu)
    except (TypeError, ValueError):
        raise ValueError("batas CPU tidak valid")
    if not 0.25 <= cpu_value <= 4:
        raise ValueError("CPU harus 0.25 sampai 4.00 core")
    memory = str(memory).strip().lower()
    storage = str(storage).strip().upper()
    if not re.fullmatch(r"(?:512|[1-9]\d*)m|(?:[1-9]\d*)g", memory):
        raise ValueError("RAM gunakan MB/GB, contoh 1500m atau 2g")
    if not re.fullmatch(r"(?:[2-9]|[1-9]\d+)G", storage):
        raise ValueError("storage gunakan GB, minimum 2G")
    return f"{cpu_value:.2f}", memory, storage


def validate_tunnel_url(url: str) -> str:
    if not isinstance(url, str) or not TUNNEL_RE.match(url):
        raise ValueError("URL Quick Tunnel tidak valid")
    return url


def set_tunnel_url(slug: str, service: str, url: str) -> None:
    """Simpan URL aktif, hanya dari supervisor untuk service yang diketahui."""
    if service not in ("hermes", "9router"):
        raise ValueError("service tunnel tidak valid")
    url = validate_tunnel_url(url)
    column = "hermes_url" if service == "hermes" else "router_url"
    with db() as c:
        c.execute(f"UPDATE instances SET {column}=? WHERE slug=?", (url, slug))


def get_instance_by_slug(slug: str) -> dict | None:
    with db() as c:
        r = c.execute("SELECT * FROM instances WHERE slug=?", (slug,)).fetchone()
    return dict(r) if r else None


def get_instance_for_user(slug: str, user_id: int, is_admin: bool) -> dict | None:
    inst = get_instance_by_slug(slug)
    if not inst:
        return None
    if not is_admin and inst["owner_id"] != user_id:
        return None
    return inst


def instance_dir(slug: str) -> str:
    return os.path.join(INSTANCES_DIR, slug)


def _create_router_key(router_port: int, router_password: str, slug: str) -> str:
    """Create a fresh key through this router's authenticated local API."""
    base = f"http://127.0.0.1:{router_port}"
    client = requests.Session()
    for _ in range(30):
        try:
            login = client.post(f"{base}/api/auth/login", json={"password": router_password}, timeout=3)
            if login.ok:
                response = client.post(f"{base}/api/keys", json={"name": f"Hermes {slug}"}, timeout=8)
                key = response.json().get("key") if response.ok else None
                if isinstance(key, str) and key:
                    return key
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError("9Router belum siap membuat API key instance")


def create_instance(slug: str, owner_id: int, dash_password: str | None = None,
                    limits: tuple[str, str, str] = DEFAULT_LIMITS,
                    expires_at: str = "lifetime") -> dict:
    """Buat stack bersih: folder data kosong + compose + up -d."""
    if dash_password is not None and len(dash_password) < 8:
        raise ValueError("password Hermes minimal 8 karakter")
    cpu_limit, memory_limit, storage_limit = validate_resource_limits(*limits)
    slug = docker_ops.validate_slug(slug)
    if get_instance_by_slug(slug):
        raise ValueError(f"instance '{slug}' sudah ada")
    d = instance_dir(slug)
    os.makedirs(os.path.join(d, "hermes-data"), exist_ok=True)
    os.makedirs(os.path.join(d, "9router-data"), exist_ok=True)
    hp, rp = next_ports()
    ctx = {
        "slug": slug,
        "hermes_image": HERMES_IMAGE,
        "router_image": ROUTER_IMAGE,
        "hermes_port": hp,
        "router_port": rp,
        "dash_password": dash_password or secrets.token_urlsafe(8),
        "router_password": secrets.token_urlsafe(8),
        "router_api_key": secrets.token_urlsafe(32),
        "auth_secret": secrets.token_urlsafe(32),
        "cpu_limit": cpu_limit,
        "memory_limit": memory_limit,
        "storage_limit": storage_limit,
    }
    compose_text = COMPOSE_TEMPLATE.format(**ctx)
    with open(os.path.join(d, "compose.yaml"), "w") as f:
        f.write(compose_text)
    with open(os.path.join(d, "hermes-data", "config.yaml"), "w") as f:
        f.write("model:\n  default: oc/hy3-free\n  provider: custom:9router\n  base_url: http://9router:20128/v1\n  api_key: ${HERMES_CUSTOM_9ROUTER_20128_API_KEY}\ncustom_providers:\n  - name: 9Router\n    base_url: http://9router:20128/v1\n    model: oc/hy3-free\n    key_env: HERMES_CUSTOM_9ROUTER_20128_API_KEY\n")
    os.chmod(os.path.join(d, "compose.yaml"), 0o600)

    r = docker_ops.compose(d, "up", "-d", timeout=600)
    if r["ok"]:
        try:
            key = _create_router_key(rp, ctx["router_password"], slug)
        except RuntimeError:
            docker_ops.compose(d, "down", "-v", "--remove-orphans", timeout=300)
            shutil.rmtree(d, ignore_errors=True)
            raise
        env_path = os.path.join(d, "hermes-data", ".env")
        with open(env_path, "w") as f:
            f.write(f"HERMES_CUSTOM_9ROUTER_20128_API_KEY={key}\n")
        os.chmod(env_path, 0o600)
        r = docker_ops.compose(d, "restart", "hermes", timeout=180)
    status = "running" if r["ok"] else "error"
    created = now()
    with db() as c:
        c.execute(
            """INSERT INTO instances(slug,owner_id,hermes_port,router_port,status,
               dashboard_password,router_password,auth_secret,created_at,cpu_limit,memory_limit,storage_limit,expires_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (slug, owner_id, hp, rp, status, ctx["dash_password"], ctx["router_password"],
             ctx["auth_secret"], created, cpu_limit, memory_limit, storage_limit, expires_at))
    if not r["ok"]:
        raise RuntimeError(f"compose up gagal: {r['err'][:300]}")
    return {"slug": slug, "hermes_port": hp, "router_port": rp, "expires_at": expires_at, **{k: ctx[k] for k in ("dash_password", "router_password")}}



def update_instance_plan(slug: str, expires_at: str) -> None:
    """Perbarui masa berlaku instance."""
    with db() as c:
        c.execute("UPDATE instances SET expires_at=? WHERE slug=?", (expires_at, slug))
    # Jika sekarang expired, pastikan container distop
    if is_instance_expired(expires_at):
        instance_action(slug, "stop")


def update_resource_limits(slug: str, cpu: str, memory: str, storage: str) -> dict:
    """Persist admin limits and recreate only this instance for CPU/RAM."""
    cpu, memory, storage = validate_resource_limits(cpu, memory, storage)
    path = os.path.join(instance_dir(slug), "compose.yaml")
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as exc:
        raise ValueError("compose instance tidak ditemukan") from exc
    text = re.sub(r"(?m)^    cpus: '[^']+'$", f"    cpus: '{cpu}'", text, count=1)
    text = re.sub(r"(?m)^    mem_limit: \S+$", f"    mem_limit: {memory}", text, count=1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)
    with db() as c:
        c.execute("UPDATE instances SET cpu_limit=?,memory_limit=?,storage_limit=? WHERE slug=?",
                  (cpu, memory, storage, slug))
    return docker_ops.compose(instance_dir(slug), "up", "-d", timeout=300)


SERVICES = ("hermes", "9router")


def _real_status(slug: str) -> str:
    """Status jujur dari kondisi nyata KEDUA kontainer."""
    up = [docker_ops.ps_one(slug, svc) for svc in SERVICES]
    if all(up):
        return "running"
    if any(up):
        return "partial"
    return "stopped"


def _ensure_all_up(slug: str) -> bool:
    """Konvergensi: pastikan KEDUA kontainer hidup.

    `docker compose start` (v2.31) terbukti intermiten hanya men-start satu
    kontainer per panggilan; kontainer yang tertinggal tidak akan di-restart
    oleh kebijakan `unless-stopped`. Periksa status nyata, start ulang yang
    mati secara langsung, lalu verifikasi ulang.
    """
    for svc in SERVICES:
        if not docker_ops.ps_one(slug, svc):
            docker_ops.start_one(slug, svc)
    return all(docker_ops.ps_one(slug, svc) for svc in SERVICES)


def instance_action(slug: str, action: str) -> dict:
    inst = get_instance_by_slug(slug)
    if not inst:
        raise ValueError("instance tidak ditemukan")
        
    # KUNCI: Jika masa berlangganan sudah habis, tolak start/restart dan paksa stop
    if is_instance_expired(inst.get("expires_at", "lifetime")):
        if action in ("start", "restart"):
            raise ValueError("Masa berlangganan instance ini telah berakhir. Hubungi administrator untuk memperpanjang.")
            
    d = instance_dir(slug)
    if action == "stop":
        r = docker_ops.compose(d, "stop", timeout=180)
        new_status = "stopped" if r["ok"] else "error"
    elif action == "start":
        r = docker_ops.compose(d, "start", timeout=180)
        if r["ok"]:
            _ensure_all_up(slug)
        new_status = _real_status(slug) if r["ok"] else "error"
    elif action == "restart":
        docker_ops.compose(d, "stop", timeout=120)
        r = docker_ops.compose(d, "start", timeout=180)
        if r["ok"]:
            _ensure_all_up(slug)
        new_status = _real_status(slug) if r["ok"] else "error"
    else:
        raise ValueError("aksi tidak valid")
    with db() as c:
        c.execute("UPDATE instances SET status=? WHERE slug=?", (new_status, slug))
    r = dict(r) if isinstance(r, dict) else {"ok": bool(r)}
    r["status"] = new_status
    return r



def update_instance_containers(slug: str) -> dict:
    """Update/recreate container instance agar menerapkan image Hermes & 9Router terbaru tanpa menghapus data."""
    inst = get_instance_by_slug(slug)
    if not inst:
        raise ValueError("instance tidak ditemukan")
    if is_instance_expired(inst.get("expires_at", "lifetime")):
        raise ValueError("Instance yang sudah kedaluwarsa tidak dapat di-update. Perpanjang masa aktif terlebih dahulu.")

    d = instance_dir(slug)
    compose_path = os.path.join(d, "compose.yaml")
    if not os.path.exists(compose_path):
        raise RuntimeError("File compose.yaml instance tidak ditemukan")

    # 1. Hentikan dan recreate container via compose (down lalu up -d)
    docker_ops.compose(d, "down", timeout=180)
    r = docker_ops.compose(d, "up", "-d", timeout=300)
    if r["ok"]:
        _ensure_all_up(slug)
    new_status = _real_status(slug) if r["ok"] else "error"
    with db() as c:
        c.execute("UPDATE instances SET status=? WHERE slug=?", (new_status, slug))
    return {"ok": r["ok"], "status": new_status, "slug": slug, "error": r.get("err")}


def update_all_instances_containers() -> dict:
    """Update seluruh instance yang aktif di server secara otomatis."""
    instances = list_instances()
    success = []
    failed = []
    for inst in instances:
        slug = inst["slug"]
        # Lewati instance yang terkunci expired
        if is_instance_expired(inst.get("expires_at", "lifetime")):
            continue
        try:
            res = update_instance_containers(slug)
            if res.get("ok"):
                success.append(slug)
            else:
                failed.append({"slug": slug, "error": res.get("error")})
        except Exception as e:
            failed.append({"slug": slug, "error": str(e)})
    return {"total": len(instances), "success": success, "failed": failed}


# Status pelacakan build image bibit di background (mencegah HTTP 524 Timeout)
_seed_build_state: dict[str, any] = {
    "running": False,
    "service": None,
    "started_at": None,
    "finished_at": None,
    "ok": None,
    "message": "",
    "details": ""
}
_seed_build_lock = threading.Lock()


def get_seed_build_status() -> dict:
    with _seed_build_lock:
        return {
            "running": _seed_build_state["running"],
            "service": _seed_build_state["service"],
            "started_at": _seed_build_state["started_at"],
            "finished_at": _seed_build_state["finished_at"],
            "last_build": {
                "ok": _seed_build_state["ok"],
                "message": _seed_build_state["message"],
                "details": _seed_build_state["details"]
            } if _seed_build_state["finished_at"] else None
        }


def _inspect_image_id(target: str, is_container: bool = False) -> str:
    """Mengambil sha ID image dan timestamp pembuatan image Docker."""
    fmt = "{{.Image}} {{.Created}}" if is_container else "{{.Id}} {{.Created}}"
    r = docker_ops._run([docker_ops.DOCKER_BIN, "inspect", "--format", fmt, target])
    return r.get("out", "").strip() if r.get("ok") else ""


def get_git_upstream_version() -> dict:
    """Mengambil commit terbaru dari git lokal dan remote upstream."""
    hermes_repo_dir = "/home/malik/docker-ai-stack/hermes-agent"
    local_commit = "unknown"
    local_time = ""
    if os.path.exists(hermes_repo_dir):
        r = docker_ops._run(["git", "-C", hermes_repo_dir, "log", "-1", "--format=%h - %s (%ci)"])
        if r["ok"] and r["out"].strip():
            local_commit = r["out"].strip()

    # Remote check via API Github (timeout cepat 3 detik)
    remote_commit = "sama dengan lokal"
    remote_sha = ""
    try:
        req = requests.get("https://api.github.com/repos/NousResearch/hermes-agent/commits/main", timeout=3.5)
        if req.status_code == 200:
            cdata = req.json()
            remote_sha = cdata.get("sha", "")[:8]
            commit_msg = cdata.get("commit", {}).get("message", "").split("\n")[0]
            commit_date = cdata.get("commit", {}).get("author", {}).get("date", "")[:16].replace("T", " ")
            remote_commit = f"{remote_sha} - {commit_msg} ({commit_date} UTC)"
    except Exception:
        pass

    return {
        "local_commit": local_commit,
        "remote_commit": remote_commit,
        "remote_sha": remote_sha
    }


def get_instance_version_info(slug: str) -> dict:
    """Mengecek apakah instance memakai image bibit terbaru (Hermes & 9Router) atau masih versi lama."""
    # 1. Cek Hermes
    h_cname = docker_ops.container_name(slug, "hermes")
    h_inst_inspect = _inspect_image_id(h_cname, is_container=True)
    h_seed_inspect = _inspect_image_id(HERMES_IMAGE, is_container=False)

    h_inst_parts = h_inst_inspect.split() if h_inst_inspect else []
    h_seed_parts = h_seed_inspect.split() if h_seed_inspect else []
    h_inst_id = h_inst_parts[0] if h_inst_parts else "unknown"
    h_seed_id = h_seed_parts[0] if h_seed_parts else "unknown"
    h_update = bool(h_inst_id and h_seed_id and h_inst_id != "unknown" and h_seed_id != "unknown" and h_inst_id != h_seed_id)

    # 2. Cek 9Router
    r_cname = docker_ops.container_name(slug, "9router")
    r_inst_inspect = _inspect_image_id(r_cname, is_container=True)
    r_seed_inspect = _inspect_image_id(ROUTER_IMAGE, is_container=False)

    r_inst_parts = r_inst_inspect.split() if r_inst_inspect else []
    r_seed_parts = r_seed_inspect.split() if r_seed_inspect else []
    r_inst_id = r_inst_parts[0] if r_inst_parts else "unknown"
    r_seed_id = r_seed_parts[0] if r_seed_parts else "unknown"
    r_update = bool(r_inst_id and r_seed_id and r_inst_id != "unknown" and r_seed_id != "unknown" and r_inst_id != r_seed_id)

    has_update = h_update or r_update

    return {
        "slug": slug,
        "has_update": has_update,
        "hermes_update": h_update,
        "router_update": r_update,
        "hermes_instance_image": h_inst_id[:19],
        "hermes_seed_image": h_seed_id[:19],
        "router_instance_image": r_inst_id[:19],
        "router_seed_image": r_seed_id[:19],
        "instance_image": h_inst_id[:19],
        "seed_image": h_seed_id[:19],
        "instance_created": h_inst_parts[-1][:16].replace("T", " ") if len(h_inst_parts) >= 2 else "-",
        "seed_created": h_seed_parts[-1][:16].replace("T", " ") if len(h_seed_parts) >= 2 else "-"
    }


def trigger_hermes_seed_rebuild_async() -> dict:
    """Memulai build image bibit Hermes di background thread agar tidak terkena Cloudflare Timeout 524."""
    with _seed_build_lock:
        if _seed_build_state["running"]:
            return {"ok": False, "message": "Proses update image bibit sedang berjalan di background."}
        _seed_build_state["running"] = True
        _seed_build_state["service"] = "hermes"
        _seed_build_state["started_at"] = now()
        _seed_build_state["ok"] = None
        _seed_build_state["message"] = "Sedang menarik source code git & me-rebuild Docker image..."

    def _worker():
        hermes_repo_dir = "/home/malik/docker-ai-stack/hermes-agent"
        try:
            # 1. Fetch/pull git
            docker_ops._run(["git", "-C", hermes_repo_dir, "pull", "origin", "main"], timeout=180)
            # 2. Rebuild
            res = docker_ops._run([docker_ops.DOCKER_BIN, "build", "-t", HERMES_IMAGE, hermes_repo_dir], timeout=900)
            with _seed_build_lock:
                _seed_build_state["running"] = False
                _seed_build_state["finished_at"] = now()
                _seed_build_state["ok"] = res["ok"]
                _seed_build_state["message"] = "Berhasil memperbarui image bibit Hermes!" if res["ok"] else "Gagal memperbarui image bibit Hermes."
                _seed_build_state["details"] = res.get("out", "")[-2000:] if res["ok"] else res.get("err", "")[-2000:]
        except Exception as e:
            with _seed_build_lock:
                _seed_build_state["running"] = False
                _seed_build_state["finished_at"] = now()
                _seed_build_state["ok"] = False
                _seed_build_state["message"] = f"Error: {e}"

    threading.Thread(target=_worker, daemon=True).start()
    return {"ok": True, "message": "Proses build image bibit Hermes telah dimulai di background."}


def rebuild_hermes_seed_image() -> dict:
    return trigger_hermes_seed_rebuild_async()



def pull_router_seed_image() -> dict:
    """Admin tool: Tarik versi terbaru image 9Router resmi dari Docker Hub."""
    pull_res = docker_ops._run([docker_ops.DOCKER_BIN, "pull", ROUTER_IMAGE], timeout=300)
    return {
        "ok": pull_res["ok"],
        "output": pull_res.get("out", "")[-1000:],
        "error": pull_res.get("err") if not pull_res["ok"] else None
    }


def destroy_instance(slug: str, wipe_data: bool) -> dict:
    """Hapus kontainer+jaringan; wipe_data=True menghapus juga folder data."""
    d = instance_dir(slug)
    compose_path = os.path.join(d, "compose.yaml")
    if os.path.isfile(compose_path):
        r = docker_ops.compose(d, "down", "-v", "--remove-orphans", timeout=300)
        if not r["ok"]:
            raise RuntimeError(f"penghapusan Docker gagal: {r.get('err', '')[:300]}")
    elif any(docker_ops.inspect_json(docker_ops.container_name(slug, service))
             for service in ("hermes", "9router")):
        raise RuntimeError("data compose hilang, tetapi container instance masih ada")
    else:
        r = {"ok": True, "out": "", "err": ""}
    if wipe_data:
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    # Retry loop aman saat delete baris DB untuk mencegah OperationalError: unable to open database file
    for attempt in range(5):
        try:
            with db() as c:
                c.execute("DELETE FROM instances WHERE slug=?", (slug,))
            break
        except sqlite3.OperationalError:
            if attempt == 4:
                raise
            time.sleep(0.5)

    return r


def refresh_status(slug: str) -> str:
    status = _real_status(slug)
    with db() as c:
        c.execute("UPDATE instances SET status=? WHERE slug=?", (status, slug))
    return status


def list_audit(limit: int = 50) -> list[dict]:
    """Ambil catatan audit terbaru untuk dashboard admin dengan format waktu Makassar (WITA, UTC+8)."""
    with db() as c:
        rows = c.execute(
            "SELECT id, ts, actor, action, detail FROM audit ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            raw_ts = d.get("ts", "")
            try:
                # Format string ISO menjadi waktu Makassar yang mudah dibaca
                dt = datetime.fromisoformat(raw_ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc).astimezone(WITA)
                else:
                    dt = dt.astimezone(WITA)
                d["ts"] = dt.strftime("%d %b %Y %H:%M:%S WITA")
            except Exception:
                pass
            result.append(d)
        return result



def get_instance_soul(slug: str) -> str:
    """Baca file SOUL.md persona/instruksi sistem dari instance."""
    soul_path = os.path.join(instance_dir(slug), "hermes-data", "SOUL.md")
    if not os.path.exists(soul_path):
        return ""
    try:
        with open(soul_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def set_instance_soul(slug: str, content: str) -> None:
    """Tulis persona/instruksi sistem ke file SOUL.md instance."""
    data_dir = os.path.join(instance_dir(slug), "hermes-data")
    os.makedirs(data_dir, exist_ok=True)
    soul_path = os.path.join(data_dir, "SOUL.md")
    with open(soul_path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        os.chmod(soul_path, 0o644)
    except Exception:
        pass


def list_pricing_plans(active_only: bool = False) -> list[dict]:
    """Ambil daftar paket berlangganan untuk halaman beranda & dashboard admin."""
    import json
    q = "SELECT * FROM pricing_plans"
    if active_only:
        q += " WHERE is_active=1"
    q += " ORDER BY order_num ASC, original_price ASC"
    with db() as c:
        rows = [dict(r) for r in c.execute(q).fetchall()]
    for r in rows:
        try:
            r["features"] = json.loads(r.get("features_json", "[]"))
        except Exception:
            r["features"] = []
    return rows


def update_pricing_plan(plan_id: str, name: str, original_price: int, promo_price: int, stock: int, is_active: int, badge: str = "", features_text: str = "") -> None:
    """Perbarui nama, fitur, harga, diskon, stok, dan status aktif paket berlangganan oleh admin."""
    import json
    # Bersihkan features text menjadi list JSON (per baris)
    features_list = [line.strip() for line in features_text.splitlines() if line.strip()] if features_text else []
    features_json = json.dumps(features_list) if features_list else None
    
    with db() as c:
        if features_json is not None:
            c.execute(
                """UPDATE pricing_plans 
                   SET name=?, original_price=?, promo_price=?, stock=?, is_active=?, badge=?, features_json=?
                   WHERE id=?""",
                (name, original_price, promo_price, stock, is_active, badge, features_json, plan_id)
            )
        else:
            c.execute(
                """UPDATE pricing_plans 
                   SET name=?, original_price=?, promo_price=?, stock=?, is_active=?, badge=?
                   WHERE id=?""",
                (name, original_price, promo_price, stock, is_active, badge, plan_id)
            )



def list_instances(owner_id: int | None = None) -> list[dict]:
    q = """SELECT i.*, u.username FROM instances i JOIN users u ON u.id=i.owner_id"""
    args: tuple = ()
    if owner_id is not None:
        q += " WHERE i.owner_id=?"
        args = (owner_id,)
    q += " ORDER BY i.id"
    with db() as c:
        rows = [dict(r) for r in c.execute(q, args).fetchall()]
    for r in rows:
        r["disk"] = docker_ops.disk_usage(instance_dir(r["slug"]))
        r["expiry"] = get_expiry_info(r.get("expires_at", "lifetime"))
        # Auto enforce expired instances to STOP
        if r["expiry"]["is_expired"] and r["status"] == "running":
            instance_action(r["slug"], "stop")
            r["status"] = "stopped"
    return rows


def instance_detail(inst: dict) -> dict:
    expiry = get_expiry_info(inst.get("expires_at", "lifetime"))
    # Auto stop jika expired saat diakses
    if expiry["is_expired"]:
        if docker_ops.ps_one(inst["slug"], "hermes") or docker_ops.ps_one(inst["slug"], "9router"):
            instance_action(inst["slug"], "stop")
            
    out = {
        "slug": inst["slug"],
        "status": refresh_status(inst["slug"]),
        "hermes_up": docker_ops.ps_one(inst["slug"], "hermes"),
        "router_up": docker_ops.ps_one(inst["slug"], "9router"),
        "hermes_port": inst["hermes_port"],
        "router_port": inst["router_port"],
        "logs_hermes": docker_ops.logs(inst["slug"], "hermes", 80),
        "logs_router": docker_ops.logs(inst["slug"], "9router", 80),
        "stats_hermes": docker_ops.stats_line(docker_ops.container_name(inst["slug"], "hermes")),
        "stats_router": docker_ops.stats_line(docker_ops.container_name(inst["slug"], "9router")),
        "disk": docker_ops.disk_usage(instance_dir(inst["slug"])),
        "current_model": get_instance_active_model(inst["slug"]),
        "soul": get_instance_soul(inst["slug"]),
        "expiry": expiry,
    }
    return out



def get_instance_active_model(slug: str) -> dict:
    """Baca model dan provider aktif dari config.yaml Hermes instance."""
    cfg_path = os.path.join(instance_dir(slug), "hermes-data", "config.yaml")
    if not os.path.exists(cfg_path):
        return {"model": "oc/muse-spark-1.2-contributor-free", "provider": "9router"}
    try:
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        m = cfg.get("model", {})
        if isinstance(m, dict):
            return {
                "model": m.get("default", "oc/muse-spark-1.2-contributor-free"),
                "provider": m.get("provider", "custom:9router"),
                "base_url": m.get("base_url", ""),
            }
        return {"model": str(m), "provider": "custom:9router"}
    except Exception:
        return {"model": "oc/muse-spark-1.2-contributor-free", "provider": "9router"}


def test_chat_instance(slug: str, messages: list[dict] | str) -> dict:
    """Kirim pesan chat test ke endpoint model aktif instance via 9Router port lokal."""
    inst = get_instance_by_slug(slug)
    if not inst:
        raise ValueError("Instance tidak ditemukan")
    
    active = get_instance_active_model(slug)
    model_name = active["model"]
    
    # Format message history
    if isinstance(messages, str):
        msg_list = [{"role": "user", "content": messages}]
    elif isinstance(messages, list):
        msg_list = messages
    else:
        msg_list = [{"role": "user", "content": str(messages)}]
    
    # Port 9Router di host
    rp = inst["router_port"]
    endpoint = f"http://127.0.0.1:{rp}/v1/chat/completions"
    
    # Ambil API key router dari .env instance
    env_path = os.path.join(instance_dir(slug), "hermes-data", ".env")
    api_key = ""
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("HERMES_CUSTOM_9ROUTER_20128_API_KEY="):
                    api_key = line.strip().split("=", 1)[1]
                    break
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    payload = {
        "model": model_name,
        "messages": msg_list,
        "max_tokens": 1000,
    }
    
    try:
        res = requests.post(endpoint, json=payload, headers=headers, timeout=45)
        raw_text = res.text
        if res.status_code == 200:
            content_type = res.headers.get("content-type", "").lower()
            reply = ""
            
            # Cek apakah response berupa Server-Sent Events (stream chunks)
            if "text/event-stream" in content_type or raw_text.strip().startswith("data:"):
                # Parse akumulasi streaming chunks SSE
                chunks = []
                for line in raw_text.splitlines():
                    line = line.strip()
                    if line.startswith("data:") and not line.endswith("[DONE]"):
                        json_str = line[len("data:"):].strip()
                        try:
                            cdata = json.loads(json_str)
                            choice = cdata.get("choices", [{}])[0]
                            delta = choice.get("delta", {})
                            content = delta.get("content")
                            # Fallback jika model mengirim content di message atau text langsung
                            if not content:
                                content = choice.get("text") or choice.get("message", {}).get("content")
                            if content:
                                chunks.append(str(content))
                        except Exception:
                            pass
                reply = "".join(chunks).strip()
            
            # Jika bukan SSE atau akumulasi SSE kosong, parse sebagai JSON biasa
            if not reply:
                clean_text = raw_text.strip()
                if "data: [DONE]" in clean_text:
                    clean_text = clean_text.split("data: [DONE]")[0].strip()
                
                try:
                    data = json.loads(clean_text)
                except Exception:
                    first_brace = clean_text.find("{")
                    last_brace = clean_text.rfind("}")
                    if first_brace != -1 and last_brace != -1:
                        data = json.loads(clean_text[first_brace:last_brace+1])
                    else:
                        raise
                choice = data.get("choices", [{}])[0]
                reply = (
                    choice.get("message", {}).get("content") or
                    choice.get("delta", {}).get("content") or
                    choice.get("text") or
                    ""
                )
            
            # Sanitasi encoding karakter non-printable / byte escape aneh jika model mengembalikan raw bytes
            if reply:
                reply = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", str(reply))
            
            return {
                "ok": True,
                "model": model_name,
                "provider": active.get("provider", ""),
                "reply": reply,
            }
        else:
            err_msg = raw_text
            try:
                err_json = res.json()
                err_msg = err_json.get("error", {}).get("message") or err_json.get("error") or err_msg
            except Exception:
                pass
            return {
                "ok": False,
                "status_code": res.status_code,
                "model": model_name,
                "error": f"HTTP {res.status_code}: {err_msg}",
            }
    except Exception as e:
        return {
            "ok": False,
            "model": model_name,
            "error": f"Gagal menghubungi model gateway: {e}",
        }

