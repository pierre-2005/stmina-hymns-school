@echo off
py -m pip install -r requirements-build.txt
py -m PyInstaller --noconfirm --clean --onefile --windowed --name StMinaHymnsManager --add-data "../app/static/images/stmina-logo.png:assets" stmina_content_manager.py
pause