#!/usr/bin/env python3
"""docker_ops.py — satu-satunya jalur eksekusi Docker untuk panel.

Aturan keamanan:
- Tidak ada shell string: setiap perintah berupa list argv.
- Allowlist operasi eksplisit; tidak ada endpoint generik "jalankan command".
- Nama instance selalu divalidasi slug ketat (a-z0-9-) sebelum dipakai.
- docker exec hanya diizinkan pada container milik instance yang diverifikasi.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import uuid

# Modul PTY POSIX (hanya ada di Linux/macOS, tidak tersedia secara bawaan di Windows)
try:
    import pty
    import tty
    import termios
    import fcntl
    _HAS_PTY = True
except ImportError:
    pty = None
    tty = None
    termios = None
    fcntl = None
    _HAS_PTY = False

def _find_docker_bin() -> str:
    """Mencari lokasi biner docker secara cerdas lintas platform."""
    # 1. Cek di PATH sistem
    which_docker = shutil.which("docker")
    if which_docker:
        return which_docker
    # 2. Cek path standar di Windows jika belum masuk ke PATH
    if sys.platform == "win32":
        win_candidates = [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Docker", "Docker", "resources", "bin", "docker.exe"),
            os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"), "Docker", "Docker", "resources", "bin", "docker.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"), "Docker", "resources", "bin", "docker.exe"),
        ]
        for c in win_candidates:
            if os.path.isfile(c):
                return c
        return "docker"
    # 3. Default path di Linux / macOS
    return "/usr/bin/docker" if os.path.isfile("/usr/bin/docker") else "docker"

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
DOCKER_BIN = _find_docker_bin()




def validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ValueError("slug tidak valid")
    return slug


def _run(argv: list[str], timeout: int = 60) -> dict:
    """Jalankan subprocess tanpa shell. Return dict hasil."""
    env = os.environ.copy()
    if sys.platform != "win32":
        env["PATH"] = "/usr/bin:/bin:/usr/local/bin:" + env.get("PATH", "")
    else:
        # Tambahkan semua kemungkinan direktori Docker Desktop & CLI di Windows
        extra_paths = [
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Docker", "Docker", "resources", "bin"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Docker", "Docker", "resources"),
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Docker", "Docker"),
            os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"), "Docker", "Docker", "resources", "bin"),
            os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"), "Docker", "Docker", "resources"),
            os.path.join(os.environ.get("ProgramW6432", r"C:\Program Files"), "Docker", "Docker"),
            os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"), "Docker", "resources", "bin"),
            os.path.join(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"), "Programs", "Docker", "Docker", "resources", "bin"),
        ]
        curr_path = env.get("PATH", "")
        for ep in extra_paths:
            if os.path.isdir(ep) and ep not in curr_path:
                curr_path = ep + os.pathsep + curr_path
        env["PATH"] = curr_path
        # Nonaktifkan credential helper eksternal sementara jika helper bermasalah di level OS Windows
        env.setdefault("DOCKER_CONFIG", os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Default"), ".docker"))
    try:
        p = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return {"ok": p.returncode == 0, "out": p.stdout[-20000:], "err": p.stderr[-8000:], "code": p.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "out": "", "err": f"timeout after {timeout}s", "code": -1}
    except FileNotFoundError as e:
        return {"ok": False, "out": "", "err": str(e), "code": -1}





def compose(project_dir: str, *args: str, timeout: int = 300) -> dict:
    """docker compose di dalam folder project instance."""
    # Pastikan kompatibilitas Docker Compose V2 (docker compose) dan V1 (docker-compose)
    compose_file = os.path.join(project_dir, "docker-compose.yml")
    if not os.path.isfile(compose_file):
        compose_file = os.path.join(project_dir, "compose.yaml")
        
    # Coba jalankan: docker compose -f <file> ...
    cmd = [DOCKER_BIN, "compose", "-f", compose_file, "--project-directory", project_dir, *args]
    r = _run(cmd, timeout=timeout)
    
    # Jika sistem Windows/Linux lama tidak mengenal subcommand 'compose' (hanya punya docker-compose biner terpisah)
    if not r["ok"] and ("is not a docker command" in r["err"] or "unknown shorthand flag" in r["err"] or "unknown flag" in r["err"]):
        dc_bin = shutil.which("docker-compose") or "docker-compose"
        alt_cmd = [dc_bin, "-f", compose_file, "--project-directory", project_dir, *args]
        r_alt = _run(alt_cmd, timeout=timeout)
        if r_alt["ok"] or r_alt["code"] == 0:
            return r_alt
            
    return r



def container_name(instance: str, service: str) -> str:
    return f"dockerman-{instance}-{service}"


def ps_one(instance: str, service: str) -> bool:
    r = _run([DOCKER_BIN, "inspect", "-f", "{{.State.Running}}", container_name(instance, service)])
    return r["ok"] and r["out"].strip() == "true"


def start_one(instance: str, service: str, timeout: int = 120) -> dict:
    """Start satu kontainer secara langsung (fallback jika compose melewatkan service)."""
    validate_slug(instance)
    return _run([DOCKER_BIN, "start", container_name(instance, service)], timeout=timeout)


def inspect_json(name: str) -> list | None:
    r = _run([DOCKER_BIN, "inspect", name])
    if not r["ok"]:
        return None
    return json.loads(r["out"])


def logs(instance: str, service: str, tail: int = 120) -> str:
    tail = max(10, min(int(tail), 500))
    r = _run([DOCKER_BIN, "logs", "--tail", str(tail), container_name(instance, service)], timeout=45)
    text = r["out"] or r["err"]
    # strip ANSI agar log aman ditampilkan mentah di browser
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def stats_line(container: str) -> dict | None:
    r = _run([DOCKER_BIN, "stats", "--no-stream", "--format",
              "{{.MemUsage}}\\t{{.CPUPerc}}", container], timeout=45)
    if not r["ok"]:
        return None
    parts = r["out"].strip().split("\t")
    if len(parts) != 2:
        return None
    return {"mem": parts[0].strip(), "cpu": parts[1].strip()}


def disk_usage(path: str) -> int:
    """Menghitung total ukuran folder data instance lintas platform."""
    if not os.path.exists(path):
        return 0
    if sys.platform != "win32" and shutil.which("du"):
        r = _run([shutil.which("du") or "/usr/bin/du", "-sb", path], timeout=60)
        if r["ok"]:
            try:
                return int(r["out"].split()[0])
            except (ValueError, IndexError):
                pass
    # Fallback portable pure-Python untuk Windows / sistem tanpa 'du'
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if not os.path.islink(fp):
                        total += os.path.getsize(fp)
                except OSError:
                    pass
    except Exception:
        pass
    return total



# Terminal PTY session management
_setup_runs: dict[str, dict] = {}
_setup_lock = threading.Lock()
_SETUP_TTL = 30 * 60  # 30 menit per sesi terminal


def _cleanup_setup_runs() -> None:
    now = time.monotonic()
    for run_id, run in list(_setup_runs.items()):
        if now - run["started"] > _SETUP_TTL or run["process"].poll() is not None:
            try:
                os.close(run["fd"])
            except OSError:
                pass
            _setup_runs.pop(run_id, None)


def start_instance_terminal(instance: str) -> str:
    """Membuka PTY Bash interaktif di dalam container Hermes (terisolasi)."""
    validate_slug(instance)
    if not ps_one(instance, "hermes"):
        raise RuntimeError("Container Hermes tidak sedang berjalan")
    if not _HAS_PTY or pty is None:
        raise RuntimeError("Fitur PTY Terminal interaktif berbasis Linux/Unix belum didukung di native Windows host. Gunakan terminal CLI langsung via 'docker exec'.")
    with _setup_lock:
        _cleanup_setup_runs()
        for run_id, run in _setup_runs.items():
            if run["instance"] == instance and run["process"].poll() is None:
                return run_id
        fd, child_fd = pty.openpty()
        
        # Set default window size (80 cols x 24 rows)
        try:
            if fcntl and termios:
                winsize = struct.pack("HHHH", 24, 80, 0, 0)
                fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass


        process = subprocess.Popen(
            [DOCKER_BIN, "exec", "-i", "-t",
             "-e", "TERM=xterm-256color",
             "-e", "COLORTERM=truecolor",
             "-e", "PATH=/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
             "-e", "HERMES_HOME=/opt/data",
             "-w", "/opt/data",
             container_name(instance, "hermes"), "/bin/bash", "-l"],
            stdin=child_fd, stdout=child_fd, stderr=child_fd,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "TERM": "xterm-256color"}, close_fds=True,
        )
        os.close(child_fd)
        os.set_blocking(fd, False)
        run_id = uuid.uuid4().hex
        _setup_runs[run_id] = {"instance": instance, "fd": fd, "process": process, "started": time.monotonic()}
        return run_id


def read_instance_terminal(run_id: str) -> dict:
    """Membaca output stream PTY terminal."""
    with _setup_lock:
        _cleanup_setup_runs()
        run = _setup_runs.get(run_id)
        if not run:
            return {"done": True, "output": "\r\n[Sesi terminal telah berakhir]\r\n"}
        output = ""
        while True:
            try:
                chunk = os.read(run["fd"], 16384)
                if not chunk:
                    break
                output += chunk.decode("utf-8", "replace")
            except BlockingIOError:
                break
            except OSError:
                break
        done = run["process"].poll() is not None
        if done:
            _cleanup_setup_runs()
        return {"done": done, "output": output}


def write_instance_terminal(run_id: str, data: str) -> None:
    """Mengirim input teks atau kontrol tombol ke PTY terminal."""
    if not isinstance(data, str) or len(data) > 8192:
        raise ValueError("input terminal tidak valid")
    with _setup_lock:
        _cleanup_setup_runs()
        run = _setup_runs.get(run_id)
        if not run:
            raise RuntimeError("Sesi terminal telah berakhir")
        os.write(run["fd"], data.encode("utf-8"))


def resize_instance_terminal(run_id: str, rows: int, cols: int) -> None:
    """Mengubah ukuran geometri terminal PTY secara dinamis."""
    with _setup_lock:
        run = _setup_runs.get(run_id)
        if not run or not fcntl or not termios:
            return
        try:
            r = max(1, min(int(rows), 200))
            c = max(1, min(int(cols), 500))
            winsize = struct.pack("HHHH", r, c, 0, 0)
            fcntl.ioctl(run["fd"], termios.TIOCSWINSZ, winsize)
        except Exception:
            pass



def stop_instance_terminal(run_id: str) -> None:
    with _setup_lock:
        run = _setup_runs.pop(run_id, None)
        if not run:
            return
        try:
            run["process"].terminate()
            run["process"].wait(timeout=2)
        except Exception:
            try:
                run["process"].kill()
            except Exception:
                pass
        try:
            os.close(run["fd"])
        except OSError:
            pass


def kill_all_setup_runs() -> None:
    """Dipanggil saat panel berhenti: pastikan tidak ada PTY docker exec yatim."""
    with _setup_lock:
        for run in _setup_runs.values():
            process = run.get("process")
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            try:
                os.close(run["fd"])
            except OSError:
                pass
        _setup_runs.clear()


# Backward compatibility aliases
start_model_setup = start_instance_terminal
read_model_setup = read_instance_terminal
write_model_setup = write_instance_terminal
stop_model_setup = stop_instance_terminal



def has_docker() -> bool:
    return shutil.which(DOCKER_BIN) is not None or shutil.which("docker") is not None


def has_image(image_name: str) -> bool:
    """Mengecek apakah image docker tertentu sudah ada di mesin lokal."""
    r = _run([DOCKER_BIN, "image", "inspect", image_name], timeout=15)
    return r["ok"] and r["code"] == 0


def os_access() -> bool:

    import os
    return os.access(DOCKER_BIN, os.X_OK)
