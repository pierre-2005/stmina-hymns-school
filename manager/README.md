# St. Mina Hymns School Content Manager

`stmina_content_manager.py` is the v3 cross-platform curriculum editor.

## Security model

- The manager does **not** store administrator passwords.
- Publishing requires an active administrator account from the website's existing Users system.
- The password is sent to `https://stminahs.overvault.ca/api/content/login` over HTTPS.
- The website returns a short-lived signed session token. The token is held in memory only.
- Only active **Administrator** accounts can validate, publish, back up content, or trigger Portainer redeploys.
- Changing/deactivating the administrator account invalidates its Content Manager session.
- GitHub and Portainer secrets stay on the Raspberry Pi/Portainer environment, not on the desktop computer.

## Run from Python

Python 3.11+ is recommended.

```bash
python stmina_content_manager.py
```

The program uses Python's standard library and Tkinter. No `pip install` is required for normal source use on standard Windows/macOS Python installations. Some Linux distributions package Tkinter separately.

## Build a standalone app

PyInstaller builds are platform-specific. Build Windows on Windows, macOS on macOS, and Linux on Linux.

```bash
python -m pip install -r requirements-build.txt
pyinstaller --noconfirm --clean --onefile --windowed --name StMinaHymnsManager stmina_content_manager.py
```

The repository also includes a GitHub Actions workflow that builds artifacts for Windows, macOS and Linux.
