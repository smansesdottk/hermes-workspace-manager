#!/usr/bin/env bash
# ==============================================================================
# Hermes Workspace Manager — Runner (Linux / Ubuntu / Debian)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Virtual environment (.venv) belum ditemukan. Menjalankan installer terlebih dahulu..."
    bash install.sh
fi

source .venv/bin/activate

# Jalankan dengan Gunicorn production server jika terpasang, atau fallback ke Flask
if command -v gunicorn &> /dev/null; then
    echo "🚀 Menjalankan Hermes Workspace Manager via Gunicorn (Port: 8899)..."
    exec gunicorn --worker-class gevent --workers 1 --timeout 120 --bind 127.0.0.1:8899 --access-logfile - --error-logfile - app:app
else
    echo "🚀 Menjalankan Hermes Workspace Manager via Flask development server (Port: 8899)..."
    exec python3 app.py
fi
