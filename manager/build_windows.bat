@echo off
py -m pip install -r requirements-build.txt
py -m PyInstaller --noconfirm --clean --onefile --windowed --name StMinaHymnsManager stmina_content_manager.py
pause
