#!/usr/bin/env bash
set -euo pipefail
python3 -m pip install -r requirements-build.txt
python3 -m PyInstaller --noconfirm --clean --onefile --windowed --name StMinaHymnsManager stmina_content_manager.py
