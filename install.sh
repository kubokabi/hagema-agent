#!/usr/bin/env bash
# ============================================================
# hagema-agent — installer satu perintah
#   ./install.sh
# Otomatis: buat venv → install package → jalankan wizard setup
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${CYAN}🐝 hagema-agent installer${NC}"
echo "----------------------------------"

# 1. Cek Python
if ! command -v python3 >/dev/null 2>&1; then
  echo -e "${YELLOW}Python3 tidak ditemukan. Install dulu: https://python.org${NC}"
  exit 1
fi

# 2. Buat venv kalau belum ada
if [ ! -d ".venv" ]; then
  echo -e "${CYAN}→ Membuat virtual environment...${NC}"
  python3 -m venv .venv
fi

# 3. Install package
echo -e "${CYAN}→ Menginstall hagema-agent...${NC}"
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e .

echo -e "${GREEN}✅ Instalasi selesai!${NC}"
echo

# 4. Setup wizard (interaktif)
if [ -t 0 ]; then
  read -r -p "Jalankan wizard setup sekarang? (pilih provider + API key) [Y/n]: " ans
  if [[ ! "$ans" =~ ^[Nn]$ ]]; then
    echo
    .venv/bin/hagema setup
  fi
fi

echo
echo -e "${GREEN}🚀 Selesai! Cara pakai:${NC}"
echo -e "   ${CYAN}source .venv/bin/activate${NC}   # aktifkan venv (sekali per terminal)"
echo -e "   ${CYAN}hagema${NC}                       # mulai ngobrol"
echo -e "   ${CYAN}hagema model${NC}                 # ganti provider/model"
echo -e "   ${CYAN}hagema doctor${NC}                # cek instalasi"
