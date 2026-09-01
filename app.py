#!/usr/bin/env python3
"""app.py — Flask panel: auth dan manajemen instance Hermes/9Router.

Keamanan:
- Session cookie HttpOnly+SameSite dan CSRF pada seluruh aksi tulis.
- User hanya melihat instance miliknya (cek ownership per request).
- Panel tidak menyediakan shell atau Docker exec untuk pengguna.
"""
from __future__ import annotations

import base64
import io
import os
import re
import secrets
import shutil
import sys
import threading
import time
import uuid

# Modul grup & user POSIX (hanya ada di Linux/macOS)
try:
    import grp
    import pwd
except ImportError:
    grp = None
    pwd = None


sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import qrcode
import requests
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from markupsafe import escape
from werkzeug.security import check_password_hash

import docker_ops
import manager

app = Flask(__name__)
_secret_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".session-secret")
if not os.path.exists(_secret_path):
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        mode = 0o600 if sys.platform != "win32" else 0o666
        fd = os.open(_secret_path, flags, mode)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(48))
    except Exception:
        with open(_secret_path, "w") as f:
            f.write(secrets.token_urlsafe(48))
with open(_secret_path) as f:
    _stored_secret = f.read().strip()
app.secret_key = os.environ.get("DM_SECRET_KEY") or _stored_secret

app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  # SSH tunnel http://127.0.0.1:8899 tetap didukung; panel publik
                  # berada di HTTPS lewat Cloudflare.
                  SESSION_COOKIE_SECURE=False, TEMPLATES_AUTO_RELOAD=True)
csrf = CSRFProtect(app)
# SSL-strict flask-wtf (wajibkan header Referer pada POST via HTTPS)
# sengaja dimatikan: di balik Cloudflare tunnel, Referer bisa hilang karena
# kebijakan browser/ekstensi privasi sehingga semua login ditolak 400
# "The referrer header is missing.". Pertahanan CSRF tetap utuh: token
# per-session + cookie SameSite=Lax.
app.config["WTF_CSRF_SSL_STRICT"] = False
# Batas maksimal upload file (misal file backup restore) = 500 MB untuk mencegah DoS disk space
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

manager.init_db()

# Batas percobaan login gagal per (username, IP) sebelum dikunci sementara.
LOGIN_MAX_FAILS = 6
LOGIN_WINDOW_SEC = 600

# Rate limiting ringan in-memory untuk aksi interaktif (Chat & Kontrol Instance)
_chat_rate_limits: dict[str, list[float]] = {}
_action_rate_limits: dict[str, float] = {}
_rate_limits_lock = threading.Lock()
CHAT_LIMIT_MAX_PER_MIN = 30
CHAT_LIMIT_WINDOW_SEC = 60.0
ACTION_MIN_INTERVAL_SEC = 1.5



@app.before_request
def _session_cookie_policy():
    """Cookie Secure dipaksa hanya pada jalur HTTPS (Cloudflare mengirim
    X-Forwarded-Proto). Akses lokal via SSH tunnel HTTP tetap berfungsi."""
    app.config["SESSION_COOKIE_SECURE"] = (
        request.headers.get("X-Forwarded-Proto", "").lower() == "https")


@app.after_request
def _security_headers(resp):
    """Header proteksi dasar; CSP konservatif agar UI inline yang ada tak rusak."""
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    # same-origin (BUKAN no-referrer): flask-wtf SSL-strict menuntut header
    # Referer pada POST via HTTPS; "no-referrer" membuat browser tak bisa
    # mengirimnya sehingga semua POST lewat Cloudflare ditolak 400
    # "The referrer header is missing."
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'")
    return resp


def _client_ip() -> str:
    """IP klien sebenarnya bila datang lewat Cloudflare, selain itu peer langsung."""
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
# Ownership metadata only; the PTY and process stay private in docker_ops.
_model_setup_runs: dict[str, dict] = {}

# Saat proses panel berhenti (Gunicorn worker maupun dev server), matikan
# semua sesi PTY `hermes setup model` agar tidak ada docker exec yatim.
import atexit as _atexit

_atexit.register(docker_ops.kill_all_setup_runs)


def current_user() -> dict | None:
    uid = session.get("uid")
    if not uid:
        return None
    with manager.db() as c:
        r = c.execute("SELECT * FROM users WHERE id=? AND is_active=1", (uid,)).fetchone()
    return dict(r) if r else None


def require_login():
    u = current_user()
    if not u:
        abort(redirect(url_for("login")))
    return u


def require_admin():
    u = require_login()
    if u["role"] != "admin":
        abort(403)
    return u


# ---------------- auth ----------------

_login_fails: dict[tuple[str, str], list[float]] = {}
_login_fails_lock = threading.Lock()


def _login_blocked(key: tuple[str, str]) -> int:
    """Return detik tersisa terkunci, 0 bila boleh coba."""
    cutoff = time.time() - LOGIN_WINDOW_SEC
    with _login_fails_lock:
        fails = [t for t in _login_fails.get(key, []) if t > cutoff]
        if fails:
            _login_fails[key] = fails
        else:
            _login_fails.pop(key, None)
    if len(fails) >= LOGIN_MAX_FAILS:
        return int(fails[0] + LOGIN_WINDOW_SEC - time.time()) + 1
    return 0


def _login_record_fail(key: tuple[str, str]) -> None:
    with _login_fails_lock:
        _login_fails.setdefault(key, []).append(time.time())


def _login_clear(key: tuple[str, str]) -> None:
    with _login_fails_lock:
        _login_fails.pop(key, None)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        key = (username, _client_ip())
        wait = _login_blocked(key)
        if wait > 0:
            manager.audit(username or "?", "login_locked",
                          f"ip={_client_ip()} tunggu={wait}s")
            flash(f"Terlalu banyak percobaan gagal. Coba lagi dalam {wait} detik.")
            return render_template("login.html")
        u = manager.get_user(username)
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            _login_clear(key)
            # Rotasi session: buang semua state lama lalu set identitas baru,
            # mencegah session fixation.
            session.clear()
            session["uid"] = u["id"]
            manager.audit(u["username"], "login")
            return redirect(url_for("dashboard"))
        _login_record_fail(key)
        manager.audit(username or "?", "login_failed", f"ip={_client_ip()}")
        flash("Username atau password salah.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account/password", methods=["POST"])
def change_password():
    u = require_login()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    if not check_password_hash(u["password_hash"], current):
        flash("Password lama tidak cocok.")
    else:
        try:
            manager.set_password(u["id"], new)
            manager.audit(u["username"], "change_password")
            flash("Password berhasil diganti.")
        except ValueError as e:
            flash(f"Gagal: {e}")
    return redirect(url_for("dashboard"))


# ---------------- dashboard & landing ----------------

@app.route("/")
def home():
    u = current_user()
    if u:
        return redirect(url_for("dashboard"))
    plans = manager.list_pricing_plans(active_only=True)
    return render_template("home.html", plans=plans)


@app.route("/dashboard")
def dashboard():
    u = require_login()
    if u["role"] == "admin":
        insts = manager.list_instances()
        users = manager.list_users()
        audits = manager.list_audit(40)
        plans = manager.list_pricing_plans(active_only=False)
    else:
        insts = manager.list_instances(u["id"])
        users = []
        audits = []
        plans = []
    return render_template("dashboard.html", me=u, instances=insts, users=users, audits=audits, plans=plans)


@app.route("/admin/pricing/<plan_id>", methods=["POST"])
def admin_update_pricing(plan_id: str):
    admin = require_admin()
    try:
        name = request.form.get("name", "").strip() or plan_id
        orig = int(request.form.get("original_price", 0) or 0)
        promo = int(request.form.get("promo_price", 0) or 0)
        stock = int(request.form.get("stock", 0) or 0)
        is_active = 1 if request.form.get("is_active") == "1" else 0
        badge = request.form.get("badge", "").strip()
        features = request.form.get("features", "").strip()
        manager.update_pricing_plan(plan_id, name, orig, promo, stock, is_active, badge, features)
        manager.audit(admin["username"], "update_pricing", f"{plan_id} name={name} promo={promo} stock={stock}")
        flash(f"Paket langganan '{name}' berhasil diperbarui.")
    except Exception as e:
        flash(f"Gagal memperbarui paket: {e}")
    return redirect(url_for("dashboard"))





# ---------------- admin ----------------

@app.route("/admin/users", methods=["POST"])
def admin_create_user():
    admin = require_admin()
    try:
        uid, pw = manager.create_user(request.form.get("username", ""),
                                      request.form.get("password") or None,
                                      request.form.get("role", "user"))
        manager.audit(admin["username"], "create_user", request.form.get("username", ""))
        flash(f"Akun dibuat. Password: {pw}")
    except ValueError as e:
        flash(f"Gagal: {e}")
    return redirect(url_for("dashboard"))


@app.route("/admin/users/<int:uid>/toggle", methods=["POST"])
def admin_toggle_user(uid: int):
    admin = require_admin()
    if uid == admin["id"]:
        flash("Tidak bisa menonaktifkan akun sendiri.")
        return redirect(url_for("dashboard"))
    with manager.db() as c:
        row = c.execute("SELECT is_active FROM users WHERE id=?", (uid,)).fetchone()
    if row:
        manager.set_user_active(uid, not row["is_active"])
        manager.audit(admin["username"], "toggle_user", f"uid={uid}")
    return redirect(url_for("dashboard"))


@app.route("/admin/users/<int:uid>/reset-password", methods=["POST"])
def admin_reset_password(uid: int):
    admin = require_admin()
    pw = manager.reset_password(uid)
    manager.audit(admin["username"], "reset_password", f"uid={uid}")
    flash(f"Password baru: {pw}")
    return redirect(url_for("dashboard"))


@app.route("/admin/users/<int:uid>/delete", methods=["POST"])
def admin_delete_user(uid: int):
    admin = require_admin()
    if uid == admin["id"]:
        flash("Tidak bisa menghapus akun sendiri.")
        return redirect(url_for("dashboard"))
    try:
        result = manager.delete_user(uid)
        manager.audit(admin["username"], "delete_user",
                      f"uid={uid} ({result['username']}); instance: "
                      f"{', '.join(result['removed_instances']) or '-'}")
        flash(f"User {result['username']} dihapus beserta "
              f"{len(result['removed_instances'])} instance-nya.")
    except ValueError as e:
        flash(f"Gagal: {e}")
    return redirect(url_for("dashboard"))


@app.route("/admin/instances", methods=["POST"])
def admin_create_instance():
    admin = require_admin()
    slug = request.form.get("slug", "").strip().lower()
    owner = request.form.get("owner", "").strip().lower()
    plan = request.form.get("plan", "lifetime").strip().lower()
    custom_months = request.form.get("custom_months", "1").strip()
    
    try:
        if not SLUG_RE.match(slug):
            raise ValueError("nama instance harus a-z0-9- (mulai huruf)")
        dash_password = request.form.get("dash_password") or None
        if dash_password and len(dash_password) < 8:
            raise ValueError("password Hermes minimal 8 karakter (kosongkan untuk acak)")
        with manager.db() as c:
            row = c.execute("SELECT id FROM users WHERE username=? AND is_active=1", (owner,)).fetchone()
        if not row:
            raise ValueError("pemilik tidak ditemukan")
            
        # Hitung masa berlaku
        if plan == "custom_m" and custom_months.isdigit():
            expires_at = manager.calculate_expiry(f"{custom_months}m")
        else:
            expires_at = manager.calculate_expiry(plan)
            
        info = manager.create_instance(slug, row["id"], dash_password, expires_at=expires_at)
        manager.audit(admin["username"], "create_instance", f"{slug} plan={plan}")
        flash(f"Instance {slug} dibuat (Masa aktif: {manager.get_expiry_info(expires_at)['label']}). Hermes: http://127.0.0.1:{info['hermes_port']} "
              f"(admin / {info['dash_password']}) — 9Router: http://127.0.0.1:{info['router_port']} "
              f"(password: {info['router_password']})")
    except (ValueError, RuntimeError) as e:
        flash(f"Gagal: {e}")
    return redirect(url_for("dashboard"))


@app.route("/admin/instances/<slug>/plan", methods=["POST"])
def admin_update_plan(slug: str):
    admin = require_admin()
    if not manager.get_instance_by_slug(slug):
        abort(404)
        
    plan = request.form.get("plan", "lifetime").strip().lower()
    custom_days = int(request.form.get("custom_days", 0) or 0)
    
    if plan == "lifetime":
        expires_at = "lifetime"
    elif plan == "custom_days" and custom_days > 0:
        expires_at = manager.calculate_expiry("custom", custom_days=custom_days)
    else:
        expires_at = manager.calculate_expiry(plan)
        
    manager.update_instance_plan(slug, expires_at)
    manager.audit(admin["username"], "update_plan", f"{slug}:{plan}")
    flash(f"Masa berlangganan instance {slug} diperbarui menjadi: {manager.get_expiry_info(expires_at)['label']}.")
    return redirect(url_for("dashboard"))



@app.route("/admin/instances/<slug>/limits", methods=["POST"])
def admin_update_limits(slug: str):
    admin = require_admin()
    if not manager.get_instance_by_slug(slug):
        abort(404)
    try:
        result = manager.update_resource_limits(slug, request.form.get("cpu", ""),
                                                request.form.get("memory", ""), request.form.get("storage", ""))
        manager.audit(admin["username"], "update_limits", slug)
        flash("Batas CPU/RAM diperbarui dan instance dimuat ulang." if result["ok"] else "Batas tersimpan, tetapi reload instance gagal.")
    except ValueError as e:
        flash(f"Gagal: {e}")
    return redirect(url_for("dashboard"))


@app.route("/admin/instances/<slug>/destroy", methods=["POST"])
def admin_destroy(slug: str):
    admin = require_admin()
    wipe = request.form.get("wipe") == "1"
    if not manager.get_instance_by_slug(slug):
        abort(404)
    manager.destroy_instance(slug, wipe)
    manager.audit(admin["username"], "destroy_instance", f"{slug} wipe={wipe}")
    flash(f"Instance {slug} dihapus{' beserta datanya' if wipe else ''}.")
    return redirect(url_for("dashboard"))


# ---------- admin instance update & seeds ----------

@app.route("/admin/instances/<slug>/update", methods=["POST"])
def admin_update_instance_containers(slug: str):
    admin = require_admin()
    try:
        res = manager.update_instance_containers(slug)
        manager.audit(admin["username"], "admin_update_instance_containers", slug)
        if res.get("ok"):
            flash(f"Instance '{slug}' berhasil di-update ke versi image terbaru!")
        else:
            flash(f"Gagal meng-update '{slug}': {res.get('error')}")
    except Exception as e:
        flash(f"Error update '{slug}': {e}")
    return redirect(url_for("dashboard"))


@app.route("/admin/instances/update-all", methods=["POST"])
def admin_update_all_instances():
    admin = require_admin()
    try:
        res = manager.update_all_instances_containers()
        manager.audit(admin["username"], "admin_update_all_instances", f"success={len(res['success'])},failed={len(res['failed'])}")
        flash(f"Update Massal Selesai: {len(res['success'])} instance berhasil di-update ({', '.join(res['success']) or 'tidak ada'}).")
    except Exception as e:
        flash(f"Error update massal: {e}")
    return redirect(url_for("dashboard"))


@app.route("/admin/seeds/hermes/update", methods=["POST"])
def admin_update_hermes_seed():
    admin = require_admin()
    res = manager.trigger_hermes_seed_rebuild_async()
    manager.audit(admin["username"], "admin_trigger_hermes_seed_rebuild", res["message"])
    flash(res["message"])
    return redirect(url_for("dashboard"))


@app.route("/admin/seeds/status")
def admin_seeds_status():
    require_admin()
    return manager.get_seed_build_status()


@app.route("/admin/seeds/versions")
def admin_seeds_versions():
    require_admin()
    return {
        "git": manager.get_git_upstream_version(),
        "seed_status": manager.get_seed_build_status()
    }


@app.route("/i/<slug>/version-check")
def instance_version_check(slug: str):
    _owned_instance(slug)
    return manager.get_instance_version_info(slug)



@app.route("/admin/seeds/9router/update", methods=["POST"])
def admin_update_router_seed():
    admin = require_admin()
    try:
        res = manager.pull_router_seed_image()
        manager.audit(admin["username"], "admin_pull_router_seed", f"ok={res['ok']}")
        if res.get("ok"):
            flash("Image bibit 9Router resmi berhasil ditarik (docker pull) versi terbarunya!")
        else:
            flash(f"Gagal menarik image 9Router: {res.get('error')}")
    except Exception as e:
        flash(f"Error pull bibit 9Router: {e}")
    return redirect(url_for("dashboard"))



# ---------------- instance ops (owner or admin) ----------------

def _owned_instance(slug: str):
    u = require_login()
    inst = manager.get_instance_for_user(slug, u["id"], u["role"] == "admin")
    if not inst:
        abort(404)
        
    # KUNCI KEAMANAN: Jika instance kedaluwarsa dan diakses oleh user biasa, blokir akses
    if u["role"] != "admin" and manager.is_instance_expired(inst.get("expires_at", "lifetime")):
        flash("Masa aktif berlangganan instance ini telah berakhir. Hubungi administrator untuk memperpanjang.")
        abort(redirect(url_for("dashboard")))
        
    return u, inst



@app.route("/i/<slug>")
def instance_page(slug: str):
    u, inst = _owned_instance(slug)
    detail = manager.instance_detail(inst)
    return render_template("instance.html", me=u, inst=inst, d=detail, active_tab="dashboard")


@app.route("/i/<slug>/settings")
def instance_settings_page(slug: str):
    u, inst = _owned_instance(slug)
    detail = manager.instance_detail(inst)
    return render_template("settings.html", me=u, inst=inst, d=detail, active_tab="settings")



@app.route("/i/<slug>/cron")
def instance_cron_page(slug: str):
    u, inst = _owned_instance(slug)
    detail = manager.instance_detail(inst)
    cron_jobs = manager.list_instance_cron_jobs(slug)
    return render_template("cron.html", me=u, inst=inst, d=detail, cron_jobs=cron_jobs, active_tab="cron")


@app.route("/i/<slug>/cron/create", methods=["POST"])
def instance_cron_create(slug: str):
    u, inst = _owned_instance(slug)
    name = request.form.get("name", "").strip()
    schedule = request.form.get("schedule", "").strip()
    prompt = request.form.get("prompt", "").strip()
    deliver = request.form.get("deliver", "origin").strip()

    if not schedule or not prompt:
        flash("Jadwal cron dan instruksi prompt wajib diisi.")
        return redirect(url_for("instance_cron_page", slug=slug))

    res = manager.create_instance_cron_job(slug, name=name, schedule=schedule, prompt=prompt, deliver=deliver)
    if res["ok"]:
        manager.audit(u["username"], "create_cron_job", f"{slug}:{res['job']['id']}")
        flash(f"Tugas terjadwal '{res['job']['name']}' berhasil dibuat.")
    else:
        flash(f"Gagal membuat tugas: {res.get('error')}")
    return redirect(url_for("instance_cron_page", slug=slug))


@app.route("/i/<slug>/cron/<job_id>/toggle", methods=["POST"])
def instance_cron_toggle(slug: str, job_id: str):
    u, inst = _owned_instance(slug)
    enable = request.form.get("enable") == "1"
    res = manager.toggle_instance_cron_job(slug, job_id, enable=enable)
    if res["ok"]:
        state_label = "diaktifkan" if enable else "dijeda"
        manager.audit(u["username"], f"cron_{'resume' if enable else 'pause'}", f"{slug}:{job_id}")
        flash(f"Tugas '{job_id}' berhasil {state_label}.")
    else:
        flash(f"Gagal mengubah status tugas: {res.get('error')}")
    return redirect(url_for("instance_cron_page", slug=slug))


@app.route("/i/<slug>/cron/<job_id>/delete", methods=["POST"])
def instance_cron_delete(slug: str, job_id: str):
    u, inst = _owned_instance(slug)
    res = manager.delete_instance_cron_job(slug, job_id)
    if res["ok"]:
        manager.audit(u["username"], "delete_cron_job", f"{slug}:{job_id}")
        flash(f"Tugas '{job_id}' berhasil dihapus.")
    else:
        flash(f"Gagal menghapus tugas: {res.get('error')}")
    return redirect(url_for("instance_cron_page", slug=slug))


@app.route("/i/<slug>/action", methods=["POST"])
def instance_action(slug: str):
    u, inst = _owned_instance(slug)
    action = request.form.get("do", "")
    if action not in ("start", "stop", "restart", "update"):
        abort(400)
        
    # Rate limit aksi instance (minimal jeda 1.5 detik antar aksi untuk mencegah race condition)
    now_ts = time.monotonic()
    with _rate_limits_lock:
        last_ts = _action_rate_limits.get(slug, 0.0)
        if now_ts - last_ts < ACTION_MIN_INTERVAL_SEC:
            flash("Mohon tunggu sejenak sebelum melakukan aksi berikutnya.")
            return redirect(url_for("instance_page", slug=slug))
        _action_rate_limits[slug] = now_ts

    if action == "update":
        try:
            r = manager.update_instance_containers(slug)
            manager.audit(u["username"], "user_update_instance_containers", slug)
            flash("Instance berhasil di-update ke versi terbaru!" if r["ok"] else f"Gagal update: {r.get('error')}")
        except Exception as e:
            flash(f"Gagal update: {e}")
        return redirect(url_for("instance_page", slug=slug))

    r = manager.instance_action(slug, action)
    manager.audit(u["username"], f"instance_{action}", slug)
    flash(f"{action}: {'OK' if r['ok'] else 'GAGAL'}")
    return redirect(url_for("instance_page", slug=slug))


@app.route("/i/<slug>/links")
def instance_links(slug: str):
    _, inst = _owned_instance(slug)
    # Ambil data instans terbaru dari DB untuk membaca URL live tunnel
    latest = manager.get_instance_by_slug(slug) or inst
    hermes_up = docker_ops.ps_one(slug, "hermes")
    router_up = docker_ops.ps_one(slug, "9router")
    active_model = manager.get_instance_active_model(slug)
    return {
        "status": manager.refresh_status(slug),
        "hermes_url": latest.get("hermes_url", ""),
        "router_url": latest.get("router_url", ""),
        "hermes_up": hermes_up,
        "router_up": router_up,
        "model": active_model.get("model", ""),
        "provider": active_model.get("provider", ""),
        "stats_hermes": docker_ops.stats_line(docker_ops.container_name(slug, "hermes")),
        "stats_router": docker_ops.stats_line(docker_ops.container_name(slug, "9router")),
        "disk": docker_ops.disk_usage(manager.instance_dir(slug)),
        "logs_hermes": docker_ops.logs(slug, "hermes", 100),
        "logs_router": docker_ops.logs(slug, "9router", 100),
    }




@app.route("/i/<slug>/channels")
def instance_channels(slug: str):
    _, inst = _owned_instance(slug)
    try:
        return _hermes_json(inst, "get", "/api/messaging/platforms")
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}, 502


@app.route("/i/<slug>/channels/<platform>", methods=["POST"])
def instance_channel_toggle(slug: str, platform: str):
    u, inst = _owned_instance(slug)
    if platform not in ("telegram", "whatsapp"):
        abort(400)
    enabled = (request.get_json(silent=True) or {}).get("enabled")
    if not isinstance(enabled, bool):
        return {"error": "status channel tidak valid"}, 400
    try:
        body: dict = {"enabled": enabled}
        # Jika menonaktifkan WhatsApp, hapus juga env WHATSAPP_ENABLED agar tidak menimpa config
        if platform == "whatsapp" and not enabled:
            body["clear_env"] = ["WHATSAPP_ENABLED", "WHATSAPP_MODE", "WHATSAPP_DM_POLICY", "WHATSAPP_ALLOWED_USERS"]
            # Bersihkan session WhatsApp lama agar gateway tidak memuat sesi stale
            wa_session_dir = os.path.join(manager.instance_dir(slug), "hermes-data", "platforms", "whatsapp", "session")
            if os.path.exists(wa_session_dir):
                try:
                    shutil.rmtree(wa_session_dir, ignore_errors=True)
                except Exception:
                    pass
            
        result = _hermes_json(inst, "put", f"/api/messaging/platforms/{platform}", body)
        manager.audit(u["username"], "channel_toggle", f"{slug}:{platform}={enabled}")
        return result
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}, 502




@app.route("/i/<slug>/gateway/restart", methods=["POST"])
def instance_gateway_restart(slug: str):
    u, inst = _owned_instance(slug)
    try:
        result = _hermes_json(inst, "post", "/api/gateway/restart", {})
        manager.audit(u["username"], "gateway_restart", slug)
        return result
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}, 502


@app.route("/i/<slug>/credentials", methods=["POST"])
def instance_credentials(slug: str):
    u, inst = _owned_instance(slug)
    manager.audit(u["username"], "reveal_credentials", slug)
    return {"hermes_username": "admin", "hermes_password": inst["dashboard_password"],
            "router_password": inst["router_password"]}


@app.route("/i/<slug>/soul", methods=["POST"])
def instance_update_soul(slug: str):
    u, inst = _owned_instance(slug)
    content = request.form.get("soul", "")
    if len(content) > 30000:
        flash("Instruksi persona terlalu panjang (maksimal 30.000 karakter).")
    else:
        manager.set_instance_soul(slug, content)
        manager.audit(u["username"], "update_soul", slug)
        flash("Persona asisten (SOUL.md) berhasil diperbarui.")
    return redirect(url_for("instance_page", slug=slug))


# Direktori cache/binari/temporary besar yang aman dilewati saat membuat backup data ringan
_BACKUP_EXCLUDE_DIR_NAMES = {
    "cache", ".cache", "lazy-packages", "node_modules", ".npm", ".local", "audio_cache", "image_cache", "session"
}
_BACKUP_EXCLUDE_EXTS = {".sock", ".pid", ".lock", ".shm", ".wal"}


@app.route("/i/<slug>/backup")
def instance_backup(slug: str):
    u, inst = _owned_instance(slug)
    manager.audit(u["username"], "download_backup", slug)
    
    inst_path = manager.instance_dir(slug)
    data_path = os.path.join(inst_path, "hermes-data")
    if not os.path.exists(data_path):
        abort(404)
        
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(data_path):
            # Prune direktori cache & temporary besar in-place
            dirs[:] = [d for d in dirs if d not in _BACKUP_EXCLUDE_DIR_NAMES and not d.startswith(".cache")]
            rel_root = os.path.relpath(root, data_path)
            arc_dir = os.path.join(f"{slug}-hermes-data", rel_root) if rel_root != "." else f"{slug}-hermes-data"
            for f in files:
                # Lewati file backup lama bertumpuk & lock/socket temporary
                if f.startswith("config.yaml.bak.") or any(f.endswith(ext) for ext in _BACKUP_EXCLUDE_EXTS):
                    continue
                full_f = os.path.join(root, f)
                arc_f = os.path.join(arc_dir, f)
                try:
                    tar.add(full_f, arcname=arc_f, recursive=False)
                except (PermissionError, OSError):
                    pass
    buf.seek(0)
    
    from flask import send_file
    return send_file(
        buf,
        mimetype="application/gzip",
        as_attachment=True,
        download_name=f"myhermes-backup-{slug}-{time.strftime('%Y%m%d_%H%M%S')}.tar.gz"
    )




# File/direktori cache atau temporary internal yang tidak perlu di-restore (dan aman dilewati jika permission terkunci)
_RESTORE_SKIP_FILES = {
    "models_dev_cache.etag", "models_dev_cache.json", "context_length_cache.yaml"
}


@app.route("/i/<slug>/restore", methods=["POST"])
def instance_restore(slug: str):
    u, inst = _owned_instance(slug)
    file = request.files.get("backup_file")
    if not file or not file.filename:
        flash("Silakan pilih file arsip backup (.tar.gz) terlebih dahulu.")
        return redirect(url_for("instance_settings_page", slug=slug))
        
    if not (file.filename.endswith(".tar.gz") or file.filename.endswith(".tgz")):
        flash("Format file tidak valid. Harap unggah file arsip .tar.gz.")
        return redirect(url_for("instance_settings_page", slug=slug))
        
    try:
        import tarfile
        inst_path = manager.instance_dir(slug)
        data_path = os.path.abspath(os.path.join(inst_path, "hermes-data"))
        os.makedirs(data_path, exist_ok=True)
        
        # Ekstrak tarfile dengan proteksi Zip-Slip, Symlink Traversal, dan File Node berbahaya
        with tarfile.open(fileobj=file.stream, mode="r:gz") as tar:
            for member in tar.getmembers():
                # Tolak tipe file berbahaya (hanya izinkan file reguler dan direktori)
                if not (member.isreg() or member.isdir()):
                    continue
                    
                # Normalisasi path
                parts = member.name.replace("\\", "/").split("/")
                if len(parts) > 1:
                    # Buang folder root pembungkus arsip (misal: slug-hermes-data)
                    rel_parts = parts[1:]
                    if not rel_parts or any(p in ("..", "", ".") for p in rel_parts):
                        continue
                    
                    filename = rel_parts[-1]
                    if filename in _RESTORE_SKIP_FILES or filename.startswith("config.yaml.bak."):
                        continue
                        
                    member.name = os.path.join(*rel_parts)
                    target_file = os.path.abspath(os.path.join(data_path, member.name))
                    
                    # Validasi Canonical Common Path (Mencegah Directory Traversal keluar dari hermes-data)
                    if not target_file.startswith(data_path + os.sep) and target_file != data_path:
                        continue
                    
                    # Jika file tujuan sudah ada dan bukan direktori, bersihkan dulu jika memungkinkan
                    if os.path.exists(target_file) and not os.path.isdir(target_file):
                        try:
                            os.remove(target_file)
                        except (PermissionError, OSError):
                            pass
                            
                    try:
                        tar.extract(member, path=data_path)
                    except (PermissionError, OSError):
                        pass
                    
        # Restart instance agar Hermes memuat data yang baru di-restore
        manager.instance_action(slug, "restart")
        manager.audit(u["username"], "restore_backup", slug)
        flash("Data berhasil dipulihkan (restore) dan instance dimuat ulang.")
    except Exception as e:
        flash(f"Gagal memulihkan backup: {e}")
        
    return redirect(url_for("instance_settings_page", slug=slug))






_hermes_sessions: dict[str, dict] = {}
_hermes_sessions_lock = threading.Lock()


def _hermes_session(inst: dict) -> requests.Session:
    """Create or reuse an authenticated local session for the instance."""
    slug = inst["slug"]
    now = time.monotonic()
    
    with _hermes_sessions_lock:
        cached = _hermes_sessions.get(slug)
        # Reuse session jika berumur kurang dari 5 menit
        if cached and (now - cached["ts"] < 300):
            return cached["client"]
            
        client = requests.Session()
        url = f"http://127.0.0.1:{inst['hermes_port']}/auth/password-login"
        
        # Coba login
        response = client.post(url, json={
            "provider": "basic",
            "username": "admin",
            "password": inst["dashboard_password"]
        }, timeout=12)
        
        if response.status_code == 429:
            # Jika terkena rate limit login sementara, reuse session lama jika ada
            if cached:
                return cached["client"]
            raise RuntimeError("Hermes sedang membatasi login (rate-limit). Tunggu beberapa detik.")
            
        if not response.ok:
            raise RuntimeError("Hermes tidak menerima sesi panel")
            
        _hermes_sessions[slug] = {"client": client, "ts": now}
        return client



def _router_models(inst: dict) -> list[str]:
    """Read 9Router's authenticated OpenAI list: only callable models."""
    env_path = os.path.join(manager.instance_dir(inst["slug"]), "hermes-data", ".env")
    try:
        with open(env_path) as f:
            text = f.read()
        key = re.search(r"^HERMES_CUSTOM_9ROUTER_20128_API_KEY=(.+)$", text, re.M).group(1).strip()
    except (OSError, AttributeError):
        raise RuntimeError("API key 9Router instance tidak tersedia")
    url = f"http://127.0.0.1:{inst['router_port']}/v1/models"
    response = requests.Session().get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    if not response.ok:
        raise RuntimeError("Daftar model aktif 9Router tidak tersedia")
    return sorted({str(row.get("id") or "") for row in response.json().get("data", []) if row.get("id")})


def _picker_options(inst: dict) -> dict:
    """Hermes' own model-picker payload, with this instance's 9Router catalog."""
    data = _hermes_json(inst, "get", "/api/model/options?include_unconfigured=true")
    for provider in data.get("providers", []):
        if provider.get("slug") == "custom:9router":
            provider["models"] = _router_models(inst)
            provider["total_models"] = len(provider["models"])
    return data


@app.route("/i/<slug>/models")
def instance_models(slug: str):
    _, inst = _owned_instance(slug)
    try:
        return _picker_options(inst)
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}, 502


@app.route("/i/<slug>/model", methods=["POST"])
def set_instance_model(slug: str):
    u, inst = _owned_instance(slug)
    body = request.get_json(silent=True) or {}
    provider_slug = str(body.get("provider", "")).strip()
    model = str(body.get("model", "")).strip()
    try:
        provider = next((p for p in _picker_options(inst).get("providers", [])
                         if p.get("slug") == provider_slug), None)
        if not provider or model not in provider.get("models", []):
            return {"error": "Model tidak tersedia untuk provider tersebut."}, 400
        if not provider.get("authenticated"):
            return {"error": provider.get("warning", "Provider ini belum tersambung.")}, 409
        result = _hermes_json(inst, "post", "/api/model/set", {
            "scope": "main", "provider": provider_slug, "model": model,
            "base_url": provider.get("api_url", "") if provider_slug.startswith("custom:") else "",
        })
        if result.get("confirm_required"):
            return {"error": result.get("confirm_message", "Konfirmasi diperlukan.")}, 409
        manager.audit(u["username"], "set_model", slug)
        return {"ok": True, "provider": provider_slug, "model": model}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}, 502


@app.route("/i/<slug>/models/quick-set", methods=["POST"])
def quick_set_model(slug: str):
    u, inst = _owned_instance(slug)
    body = request.get_json(silent=True) or {}
    model_name = str(body.get("model", "")).strip()
    if not model_name:
        return {"error": "Nama model tidak boleh kosong."}, 400
        
    cfg_path = os.path.join(manager.instance_dir(slug), "hermes-data", "config.yaml")
    if not os.path.exists(cfg_path):
        return {"error": "Konfigurasi instance belum tersedia."}, 404
        
    try:
        import yaml
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
            
        m = cfg.setdefault("model", {})
        if not isinstance(m, dict):
            m = {"provider": "custom", "base_url": "http://9router:20128/v1", "api_key": "${HERMES_CUSTOM_9ROUTER_20128_API_KEY}"}
            cfg["model"] = m
            
        m["default"] = model_name
        
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)
            
        manager.audit(u["username"], "quick_set_model", f"{slug}:{model_name}")
        return {"ok": True, "model": model_name}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/i/<slug>/terminal")
def instance_terminal_page(slug: str):
    u, inst = _owned_instance(slug)
    detail = manager.instance_detail(inst)
    return render_template("terminal.html", me=u, inst=inst, d=detail, active_tab="terminal")


@app.route("/i/<slug>/terminal/session", methods=["POST"])
def instance_terminal_start(slug: str):
    u, inst = _owned_instance(slug)
    try:
        run_id = docker_ops.start_instance_terminal(slug)
        _model_setup_runs[run_id] = {"owner_id": u["id"], "slug": inst["slug"]}
        manager.audit(u["username"], "terminal_start", slug)
        return {"ok": True, "id": run_id}
    except RuntimeError as e:
        return {"error": str(e)}, 409


def _owned_setup_run(slug: str, run_id: str) -> dict:
    u, _ = _owned_instance(slug)
    run = _model_setup_runs.get(run_id)
    if not run or run["owner_id"] != u["id"] or run["slug"] != slug:
        abort(404)
    return run


@app.route("/i/<slug>/terminal/read/<run_id>")
def instance_terminal_read(slug: str, run_id: str):
    _owned_setup_run(slug, run_id)
    data = docker_ops.read_instance_terminal(run_id)
    if data["done"]:
        _model_setup_runs.pop(run_id, None)
    return data


@app.route("/i/<slug>/terminal/write/<run_id>", methods=["POST"])
def instance_terminal_write(slug: str, run_id: str):
    _owned_setup_run(slug, run_id)
    data = (request.get_json(silent=True) or {}).get("input", "")
    if not isinstance(data, str) or len(data) > 8192:
        return {"error": "input terminal tidak valid"}, 400
    try:
        docker_ops.write_instance_terminal(run_id, data)
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}, 400
    return {"ok": True}


@app.route("/i/<slug>/terminal/resize/<run_id>", methods=["POST"])
def instance_terminal_resize(slug: str, run_id: str):
    _owned_setup_run(slug, run_id)
    body = request.get_json(silent=True) or {}
    rows = body.get("rows", 24)
    cols = body.get("cols", 80)
    docker_ops.resize_instance_terminal(run_id, rows, cols)
    return {"ok": True}


@app.route("/i/<slug>/terminal/stop/<run_id>", methods=["POST", "DELETE"])
def instance_terminal_stop(slug: str, run_id: str):
    _owned_setup_run(slug, run_id)
    docker_ops.stop_instance_terminal(run_id)
    _model_setup_runs.pop(run_id, None)
    return {"ok": True}


# Backward compatibility routes
@app.route("/i/<slug>/model/setup", methods=["POST"])
def model_setup_start(slug: str):
    return instance_terminal_start(slug)


@app.route("/i/<slug>/model/setup/<run_id>")
def model_setup_read(slug: str, run_id: str):
    return instance_terminal_read(slug, run_id)


@app.route("/i/<slug>/model/setup/<run_id>", methods=["DELETE"])
def model_setup_stop(slug: str, run_id: str):
    return instance_terminal_stop(slug, run_id)


@app.route("/i/<slug>/model/setup/<run_id>/input", methods=["POST"])
def model_setup_input(slug: str, run_id: str):
    return instance_terminal_write(slug, run_id)


@app.route("/i/<slug>/model/chat", methods=["POST"])
def model_test_chat(slug: str):
    u, inst = _owned_instance(slug)
    
    # Rate limit chat (maksimal 30 pesan per menit per user/instance)
    now_ts = time.time()
    cutoff = now_ts - CHAT_LIMIT_WINDOW_SEC
    key = f"{u['id']}:{slug}"
    with _rate_limits_lock:
        timestamps = [t for t in _chat_rate_limits.get(key, []) if t > cutoff]
        if len(timestamps) >= CHAT_LIMIT_MAX_PER_MIN:
            return {"error": "Pengiriman pesan terlalu cepat. Silakan tunggu 1 menit."}, 429
        timestamps.append(now_ts)
        _chat_rate_limits[key] = timestamps

    body = request.get_json(silent=True) or {}

    
    # Dukung pengiriman string tunggal atau array riwayat percakapan
    messages = body.get("messages")
    if not messages:
        single_msg = str(body.get("message", "")).strip()
        if not single_msg:
            return {"error": "Pesan tidak boleh kosong"}, 400
        messages = [{"role": "user", "content": single_msg}]
    elif isinstance(messages, list):
        # Validasi struktur messages
        sanitized = []
        for m in messages:
            if isinstance(m, dict) and m.get("role") in ("user", "assistant", "system") and m.get("content"):
                sanitized.append({"role": m["role"], "content": str(m["content"])[:4000]})
        if not sanitized:
            return {"error": "Riwayat percakapan tidak valid"}, 400
        messages = sanitized[-12:]  # Batasi 12 pesan terakhir agar efisien
    else:
        return {"error": "Format data chat tidak valid"}, 400
    
    manager.audit(u["username"], "test_chat", slug)
    result = manager.test_chat_instance(slug, messages)
    if result["ok"]:
        return result
    return result, 502



@app.route("/i/<slug>/model/key", methods=["POST"])
def set_provider_key(slug: str):
    u, inst = _owned_instance(slug)
    body = request.get_json(silent=True) or {}
    provider_slug = str(body.get("provider", "")).strip()
    key = str(body.get("key", "")).strip()
    provider = next((p for p in _picker_options(inst).get("providers", [])
                     if p.get("slug") == provider_slug), None)
    env_key = provider.get("key_env", "") if provider else ""
    if not env_key or not key:
        return {"error": "Provider atau API key tidak valid."}, 400
    try:
        _hermes_json(inst, "put", "/api/env", {"key": env_key, "value": key})
        manager.audit(u["username"], "set_provider_key", f"{slug}:{provider_slug}")
        return {"ok": True}
    except (ValueError, RuntimeError) as e:
        return {"error": str(e)}, 502


def _qr_data_url(payload: str) -> str:
    image = qrcode.make(payload)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def _hermes_json(inst: dict, method: str, path: str, body: dict | None = None) -> dict:
    response = getattr(_hermes_session(inst), method.lower())(
        f"http://127.0.0.1:{inst['hermes_port']}{path}", json=body, timeout=20)
    if not response.ok:
        try:
            message = response.json().get("detail") or response.json().get("error")
        except ValueError:
            message = "Permintaan Hermes gagal"
        raise ValueError(str(message or "Permintaan Hermes gagal"))
    return response.json()


@app.route("/i/<slug>/telegram/manual", methods=["POST"])
def telegram_manual(slug: str):
    u, inst = _owned_instance(slug)
    token = request.form.get("token", "").strip()
    allowed = request.form.get("allowed_user", "").strip()
    if not token or not allowed.isdigit():
        flash("Token bot dan ID Telegram numerik wajib diisi.")
    else:
        try:
            _hermes_json(inst, "put", "/api/messaging/platforms/telegram", {
                "enabled": True, "env": {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ALLOWED_USERS": allowed}})
            _hermes_json(inst, "post", "/api/gateway/restart", {})
            manager.audit(u["username"], "telegram_manual", slug)
            flash("Telegram tersambung. Hermes sedang memuat ulang gateway.")
        except (ValueError, RuntimeError) as e:
            flash(f"Telegram gagal: {e}")
    return redirect(url_for("instance_page", slug=slug))


@app.route("/i/<slug>/whatsapp/onboarding", methods=["POST"])
def whatsapp_onboarding(slug: str):
    u, inst = _owned_instance(slug)
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode", "bot")).strip().lower()
    if mode not in ("bot", "self-chat"):
        mode = "bot"
    allowed_users = str(body.get("allowed_users", "")).strip()
    force_relink = bool(body.get("force_relink", True))
    
    # Bersihkan file session creds WhatsApp lama di container jika user meminta pairing ulang
    if force_relink:
        wa_session_dir = os.path.join(manager.instance_dir(slug), "hermes-data", "platforms", "whatsapp", "session")
        if os.path.exists(wa_session_dir):
            try:
                shutil.rmtree(wa_session_dir, ignore_errors=True)
            except Exception:
                pass
    
    try:
        data = _hermes_json(inst, "post", "/api/messaging/whatsapp/onboarding/start", {
            "mode": mode,
            "allowed_users": allowed_users,
        })
        session[f"wa:{slug}"] = {
            "id": data["pairing_id"],
            "mode": mode,
            "allowed_users": allowed_users,
            "expires": time.time() + 600,
        }
        manager.audit(u["username"], "whatsapp_qr_start", f"{slug}:mode={mode}")
        return {"ok": True, "qr": _qr_data_url(data.get("qr_payload", "")), "status": data.get("status", "starting")}
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}, 400




@app.route("/i/<slug>/telegram/onboarding", methods=["POST"])
def telegram_onboarding(slug: str):
    u, inst = _owned_instance(slug)
    try:
        data = _hermes_json(inst, "post", "/api/messaging/telegram/onboarding/start",
                            {"bot_name": f"Hermes {inst['slug']}"})
        session[f"tg:{slug}"] = {"id": data["pairing_id"], "expires": time.time() + 600}
        manager.audit(u["username"], "telegram_qr_start", slug)
        return {"ok": True, "qr": _qr_data_url(data["qr_payload"]), "deep_link": data["deep_link"]}
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}, 400




@app.route("/i/<slug>/whatsapp/onboarding/status")
def whatsapp_onboarding_status(slug: str):
    u, inst = _owned_instance(slug)
    record = session.get(f"wa:{slug}") or {}
    if record.get("expires", 0) < time.time():
        return {"status": "expired"}, 410
    try:
        data = _hermes_json(inst, "get", f"/api/messaging/whatsapp/onboarding/{record['id']}")
        result = {}
        if data.get("status") == "connected":
            mode = record.get("mode", "bot")
            allowed = record.get("allowed_users", "")
            # Jika mode bot dan allowed_users kosong, gunakan nomor akun yang men-scan
            if mode == "bot" and not allowed:
                allowed = str(data.get("account_phone") or data.get("account_id") or "").strip()
            
            result = _hermes_json(inst, "post", f"/api/messaging/whatsapp/onboarding/{record['id']}/apply",
                                  {"mode": mode, "allowed_users": allowed})
            session.pop(f"wa:{slug}", None)
            manager.audit(u["username"], "whatsapp_qr_apply", f"{slug}:mode={mode}")
        return {"status": data.get("status", "waiting"), "qr": _qr_data_url(data["qr_payload"]) if data.get("qr_payload") else "", "restart_started": result.get("restart_started")}
    except (ValueError, RuntimeError, KeyError) as e:
        return {"status": "error", "error": str(e)}, 400




@app.route("/i/<slug>/logs")
def instance_logs(slug: str):
    u, inst = _owned_instance(slug)
    service = request.args.get("s", "hermes")
    if service not in ("hermes", "9router"):
        abort(400)
    tail = request.args.get("tail", "120")
    if not tail.isdigit():
        abort(400)
    return {"logs": docker_ops.logs(slug, service, int(tail))}


# ---------------- error pages ----------------

@app.errorhandler(403)
def e403(_):
    return render_template("error.html", code=403, msg="Khusus admin."), 403


@app.errorhandler(404)
def e404(_):
    return render_template("error.html", code=404, msg="Tidak ditemukan."), 404


if __name__ == "__main__":
    # drop ke grup docker bila dijalankan sebagai root (systemd di Linux)
    if grp and pwd and hasattr(os, "geteuid") and hasattr(os, "setgid"):
        try:
            gid = grp.getgrnam("docker").gr_gid
            if os.geteuid() == 0:
                os.setgroups([gid])
                os.setgid(gid)
                os.setuid(1000)
        except Exception:
            pass
    app.run(host="127.0.0.1", port=8899, debug=False)

