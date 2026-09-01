#!/usr/bin/env python3
"""Menjaga dua Quick Tunnel terisolasi untuk setiap instance DockerMan.

Hanya memulai `/usr/local/bin/cloudflared --config /dev/null tunnel --url ...`.
Tidak membaca/menulis ~/.cloudflared dan tidak mengelola proses cloudflared lain.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import docker_ops
import manager

CLOUDFLARED = "/usr/local/bin/cloudflared"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
RUNNING = True
CHILDREN: dict[tuple[str, str], subprocess.Popen] = {}


def stop_children(*_):
    global RUNNING
    RUNNING = False
    for p in list(CHILDREN.values()):
        if p.poll() is None:
            p.terminate()


def start(slug: str, service: str, port: int) -> subprocess.Popen:
    d = Path(manager.instance_dir(slug)) / "tunnel-logs"
    d.mkdir(mode=0o700, exist_ok=True)
    log = open(d / f"{service}.log", "a", buffering=1)
    return subprocess.Popen(
        [CLOUDFLARED, "--config", "/dev/null", "tunnel", "--url",
         f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=log, stderr=subprocess.STDOUT, close_fds=True,
    )


def harvest(slug: str, service: str) -> None:
    path = Path(manager.instance_dir(slug)) / "tunnel-logs" / f"{service}.log"
    try:
        found = URL_RE.findall(path.read_text(errors="replace"))
        if found:
            manager.set_tunnel_url(slug, service, found[-1])
    except (OSError, ValueError):
        pass


def reconcile_once() -> None:
    instances = manager.list_instances()
    active = {inst["slug"] for inst in instances}
    for key, p in list(CHILDREN.items()):
        if key[0] not in active:
            if p.poll() is None:
                p.terminate()
            CHILDREN.pop(key, None)
    for inst in instances:
        slug = inst["slug"]
        wanted = (("hermes", inst["hermes_port"]), ("9router", inst["router_port"]))
        for service, port in wanted:
            key = (slug, service)
            # Jangan buka tunnel untuk layanan container yang dimatikan user atau kedaluwarsa.
            if not docker_ops.ps_one(slug, service) or inst.get("expiry", {}).get("is_expired"):
                p = CHILDREN.pop(key, None)
                if p and p.poll() is None:
                    p.terminate()
                continue
            p = CHILDREN.get(key)
            # Jika proses tunnel mati atau hang, restart tunnel baru
            if not p or p.poll() is not None:
                # Bersihkan log lama agar URL baru langsung terbaca
                log_path = Path(manager.instance_dir(slug)) / "tunnel-logs" / f"{service}.log"
                if log_path.exists():
                    try:
                        log_path.unlink()
                    except Exception:
                        pass
                CHILDREN[key] = start(slug, service, port)
            harvest(slug, service)



def main() -> int:
    # Pastikan migrasi schema selesai sebelum membaca/menulis URL tunnel.
    manager.init_db()
    if not os.path.isfile(CLOUDFLARED):
        print(f"cloudflared tidak ditemukan: {CLOUDFLARED}", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)
    while RUNNING:
        reconcile_once()
        time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
