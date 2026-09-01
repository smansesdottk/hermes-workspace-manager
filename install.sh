#!/usr/bin/env bash
# ==============================================================================
# Hermes Workspace Manager — 1-Click Installer (Linux / Ubuntu / Debian)
# ==============================================================================
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}================================================================${NC}"
echo -e "${CYAN}    🚀 Hermes Workspace Manager — 1-Click Setup & Installer     ${NC}"
echo -e "${CYAN}================================================================${NC}"
echo ""

# 1. Cek Python 3
echo -e "🔍 [1/5] Memeriksa Python 3..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python 3 tidak ditemukan. Harap instal Python 3 terlebih dahulu.${NC}"
    echo "Contoh: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}✓ Python $PY_VER terdeteksi.${NC}"

# 2. Cek Docker
echo -e "\n🔍 [2/5] Memeriksa Docker & Docker Compose..."
if ! command -v docker &> /dev/null; then
    echo -e "${YELLOW}⚠️ Docker CLI tidak ditemukan di PATH.${NC}"
    echo "Pastikan Docker Engine sudah terpasang dan service docker aktif."
else
    echo -e "${GREEN}✓ Docker terdeteksi: $(docker --version)${NC}"
fi

# 3. Setup Python Virtual Environment
echo -e "\n📦 [3/5] Menyiapkan Virtual Environment (.venv)..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
    echo "Membuat virtual environment baru di .venv..."
    python3 -m venv .venv
fi

# Aktivasi venv
source .venv/bin/activate
pip install --upgrade pip --quiet
echo "Memasang dependensi dari requirements.txt..."
pip install -r requirements.txt --quiet
echo -e "${GREEN}✓ Dependensi Python berhasil dipasang.${NC}"

# 4. Inisialisasi Database & Secret Key
echo -e "\n🔐 [4/5] Menginisialisasi Database & Security Keys..."
python3 -c "import manager; manager.init_db(); print('✓ Database panel.db terinisialisasi.')"

# 5. Tarik Image Docker Master (9Router)
echo -e "\n🐳 [5/5] Memeriksa & Menarik Image Docker Bibit..."
if command -v docker &> /dev/null; then
    echo "Menarik image 9Router resmi (decolua/9router:latest)..."
    docker pull decolua/9router:latest || echo -e "${YELLOW}⚠️ Gagal menarik image 9router (bisa ditarik nanti lewat dashboard admin).${NC}"
fi

echo ""
echo -e "${GREEN}================================================================${NC}"
echo -e "${GREEN}       🎉 INSTALASI SELESAI & SIAP DIGUNAKAN!                    ${NC}"
echo -e "${GREEN}================================================================${NC}"
echo ""
echo -e "Untuk menjalankan panel, jalankan:"
echo -e "  ${CYAN}./run.sh${NC}   atau   ${CYAN}bash run.sh${NC}"
echo ""
echo -e "Akses web browser di: ${CYAN}http://127.0.0.1:8899${NC}"
echo ""
