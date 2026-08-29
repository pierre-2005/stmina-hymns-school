from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import tkinter as tk
import unicodedata
import uuid
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk, font as tkfont
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

APP_NAME = "St. Mina Hymns School Content Manager"
APP_VERSION = "4.0"
DEFAULT_SITE_URL = "https://stminahs.overvault.ca"
SETTINGS_DIR = Path.home() / ".stmina-hymns-manager"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


# Website-matched visual theme
BURGUNDY_950 = "#35101b"
BURGUNDY_900 = "#4d1627"
BURGUNDY_800 = "#632036"
BURGUNDY_700 = "#7a263f"
BURGUNDY_600 = "#91344d"
ROSE_100 = "#f6e9ec"
ROSE_50 = "#fcf6f7"
CREAM = "#fffaf4"
PAPER = "#ffffff"
INK = "#201a1c"
MUTED = "#6f6669"
LINE = "#e9dfe1"
SUCCESS = "#276749"
DANGER = "#a12b3c"

# ---------------------------------------------------------------------------
# Unicode Coptic -> Avva Shenouda legacy mapping
# Integrated from the user's standalone converter.
# ---------------------------------------------------------------------------

UNICODE_TO_AVVA = {
    "Ⲁ": "A", "ⲁ": "a",
    "Ⲃ": "B", "ⲃ": "b",
    "Ⲅ": "J", "ⲅ": "j",
    "Ⲇ": "D", "ⲇ": "d",
    "Ⲉ": "E", "ⲉ": "e",
    "Ⲍ": "Z", "ⲍ": "z",
    "Ⲏ": "#", "ⲏ": "3",
    "Ⲑ": ")", "ⲑ": "0",
    "Ⲓ": "I", "ⲓ": "i",
    "Ⲕ": "K", "ⲕ": "k",
    "Ⲗ": "L", "ⲗ": "l",
    "Ⲙ": "M", "ⲙ": "m",
    "Ⲛ": "N", "ⲛ": "n",
    "Ⲝ": "&", "ⲝ": "7",
    "Ⲟ": "O", "ⲟ": "o",
    "Ⲡ": "P", "ⲡ": "p",
    "Ⲣ": "R", "ⲣ": "r",
    "Ⲥ": "C", "ⲥ": "c",
    "Ⲧ": "T", "ⲧ": "t",
    "Ⲩ": "V", "ⲩ": "v",
    "Ⲫ": "F", "ⲫ": "f",
    "Ⲭ": "X", "ⲭ": "x",
    "Ⲯ": "Y", "ⲯ": "y",
    "Ⲱ": "W", "ⲱ": "w",
    "Ϣ": "@", "ϣ": "2",
    "Ϥ": "$", "ϥ": "4",
    "Ϧ": "Q", "ϧ": "q",
    "Ϩ": "H", "ϩ": "h",
    "Ϫ": "G", "ϫ": "g",
    "Ϭ": "S", "ϭ": "s",
    "Ϯ": "%", "ϯ": "5",
    "ⲋ": "6",
    "⳥": "U",
    "⳪": "u",
    "ⳮ": "+",
}

SPECIAL_SEQUENCES = {
    # Avva Shenouda has dedicated legacy glyph slots for overlined delta and
    # upsilon. Coptic sources use several canonically different overline marks,
    # so accept all three variants instead of only U+0305. This keeps the web
    # renderer consistent with the Content Manager preview for abbreviations.
    "ⲇ\u0304": "ä",
    "ⲇ\u0305": "ä",
    "ⲇ\u033f": "ä",
    "ⲩ\u0304": "ö",
    "ⲩ\u0305": "ö",
    "ⲩ\u033f": "ö",
}

AVVA_TO_UNICODE = {value: key for key, value in UNICODE_TO_AVVA.items()}
AVVA_SPECIAL_TO_UNICODE = {value: key for key, value in SPECIAL_SEQUENCES.items()}


def contains_unicode_coptic(text: str) -> bool:
    """Return True when text contains Unicode Coptic characters."""
    for ch in str(text or ""):
        cp = ord(ch)
        if 0x2C80 <= cp <= 0x2CFF or 0x03E2 <= cp <= 0x03EF:
            return True
    return False


def unicode_coptic_to_avva_runs(
    text: str,
) -> list[tuple[str, str]]:
    """
    Convert Unicode Coptic to Avva preview runs.

    "avva" runs should use Avva Shenouda.
    "plain" runs should use the normal interface font.
    """

    text = unicodedata.normalize(
        "NFD",
        str(text or ""),
    )

    runs: list[tuple[str, str]] = []

    def append_run(
        kind: str,
        value: str,
    ) -> None:
        if not value:
            return

        if runs and runs[-1][0] == kind:
            runs[-1] = (
                kind,
                runs[-1][1] + value,
            )
        else:
            runs.append(
                (kind, value)
            )

    i = 0

    while i < len(text):

        if i + 1 < len(text):
            pair = text[i:i + 2]

            if pair in SPECIAL_SEQUENCES:
                append_run(
                    "avva",
                    SPECIAL_SEQUENCES[pair],
                )

                i += 2
                continue

        ch = text[i]

        if ch in UNICODE_TO_AVVA:
            mapped = UNICODE_TO_AVVA[ch]

            j = i + 1

            combining_marks: list[str] = []

            while (
                j < len(text)
                and unicodedata.combining(text[j])
            ):
                combining_marks.append(
                    text[j]
                )

                j += 1

            prefix = ""
            suffix = ""

            for mark in combining_marks:

                if mark == "\u0300":
                    prefix += "`"

                else:
                    suffix += mark

            append_run(
                "avva",
                prefix + mapped + suffix,
            )

            i = j
            continue

        # Literal parentheses and every other
        # non-Coptic character stay plain.
        append_run(
            "plain",
            ch,
        )

        i += 1

    return runs


def unicode_coptic_to_avva(
    text: str,
) -> str:
    return "".join(
        value
        for _kind, value
        in unicode_coptic_to_avva_runs(text)
    )

def avva_to_unicode_coptic(text: str) -> str:
    """
    Best-effort conversion of OLD legacy Avva text back to Unicode Coptic.

    New Content Manager rows are stored as Unicode, so if Unicode Coptic is
    already present we return it unchanged. Matched (), [], and {} delimiters
    are preserved as literal punctuation when converting older legacy rows.
    """
    text = str(text or "")

    # Never reinterpret a new Unicode row as legacy ASCII. Doing so would turn
    # ordinary Latin/punctuation inside a Unicode row into Coptic letters.
    if contains_unicode_coptic(text):
        return unicodedata.normalize("NFC", text)

    output: list[str] = []
    expected_closers: list[str] = []
    opening_to_closing = {"(": ")", "[": "]", "{": "}"}
    i = 0

    while i < len(text):
        ch = text[i]

        if ch in opening_to_closing:
            expected_closers.append(opening_to_closing[ch])
            output.append(ch)
            i += 1
            continue

        if expected_closers and ch == expected_closers[-1]:
            expected_closers.pop()
            output.append(ch)
            i += 1
            continue

        if ch in AVVA_SPECIAL_TO_UNICODE:
            output.append(AVVA_SPECIAL_TO_UNICODE[ch])
            i += 1
            continue

        # Avva grave accent: the legacy backtick comes BEFORE the glyph.
        if ch == "`" and i + 1 < len(text):
            next_ch = text[i + 1]
            if next_ch in AVVA_TO_UNICODE:
                output.append(AVVA_TO_UNICODE[next_ch] + "\u0300")
                i += 2
                continue

        if ch in AVVA_TO_UNICODE:
            output.append(AVVA_TO_UNICODE[ch])
        else:
            output.append(ch)

        i += 1

    return unicodedata.normalize("NFC", "".join(output))


def find_logo_path() -> Path | None:
    """Find the St. Mina logo in source mode or inside a PyInstaller bundle."""
    candidates: list[Path] = []

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.append(Path(bundle_root) / "assets" / "stmina-logo.png")

    here = Path(__file__).resolve().parent
    candidates.extend(
        [
            here / "assets" / "stmina-logo.png",
            here.parent / "app" / "static" / "images" / "stmina-logo.png",
            here / "stmina-logo.png",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_scaled_png(path: Path, target_px: int = 58) -> tk.PhotoImage:
    """Load a PNG using Tk only and shrink it near target_px."""
    image = tk.PhotoImage(file=str(path))
    largest = max(image.width(), image.height())
    if largest > target_px:
        factor = max(1, round(largest / target_px))
        image = image.subsample(factor, factor)
    return image



class ApiError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ContentApiClient:
    def __init__(self, base_url: str):
        self.base_url = self._normalise_base_url(base_url)
        self.token = ""
        self.user: dict[str, Any] | None = None

    @staticmethod
    def _normalise_base_url(value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            raise ValueError("Website URL is required.")
        if "://" not in value:
            value = "https://" + value
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Enter a valid website URL, such as https://stminahs.overvault.ca")
        return value

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        auth: bool = True,
        timeout: int = 10,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"stmina-content-manager/{APP_VERSION}",
        }
        if auth:
            if not self.token:
                raise ApiError("Sign in before using this action.", 401)
            headers["Authorization"] = f"Bearer {self.token}"
        payload = json.dumps(data).encode("utf-8") if data is not None else None
        request = Request(self.base_url + path, data=payload, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {"ok": True}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw
            try:
                parsed = json.loads(raw)
                detail = parsed.get("detail") or parsed.get("message") or raw
            except Exception:
                pass
            raise ApiError(str(detail) or f"HTTP {exc.code}", exc.code) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ApiError(
                f"The website took too long to respond.\n\nWebsite: {self.base_url}"
            ) from exc
        except URLError as exc:
            raise ApiError(f"Could not connect to {self.base_url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ApiError("The website returned an invalid response.") from exc

    def _request_file(
        self,
        path: str,
        field_name: str,
        file_path: str | Path,
        timeout: int = 60,
    ) -> dict[str, Any]:
        if not self.token:
            raise ApiError("Sign in before using this action.", 401)

        source = Path(file_path)
        try:
            file_bytes = source.read_bytes()
        except OSError as exc:
            raise ApiError(f"Could not read {source.name}: {exc}") from exc

        boundary = f"----StMinaContentManager{uuid.uuid4().hex}"
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        safe_name = source.name.replace('"', "_")

        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode("ascii"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{safe_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        body.extend(file_bytes)
        body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))

        headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": f"stmina-content-manager/{APP_VERSION}",
        }
        request = Request(
            self.base_url + path,
            data=bytes(body),
            headers=headers,
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {"ok": True}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw
            try:
                parsed = json.loads(raw)
                detail = parsed.get("detail") or parsed.get("message") or raw
            except Exception:
                pass
            raise ApiError(str(detail) or f"HTTP {exc.code}", exc.code) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ApiError(
                f"The website took too long to process {source.name}."
            ) from exc
        except URLError as exc:
            raise ApiError(f"Could not connect to {self.base_url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise ApiError("The website returned an invalid file-processing response.") from exc

    def ocr_english_image(self, file_path: str | Path) -> dict[str, Any]:
        return self._request_file(
            "/api/content/ocr/english",
            "image",
            file_path,
            timeout=60,
        )

    def import_youtube_audio(self, url: str) -> dict[str, Any]:
        started = self._request(
            "POST",
            "/api/content/audio/import-youtube/start",
            {"url": url, "confirm_rights": True},
            timeout=20,
        )
        job_id = str(started.get("job_id", "")).strip()
        if not job_id:
            raise ApiError("The website did not return a YouTube import job ID.")

        deadline = time.monotonic() + 30 * 60
        while time.monotonic() < deadline:
            result = self._request(
                "GET",
                f"/api/content/audio/import-youtube/status/{job_id}",
                timeout=15,
            )
            state = str(result.get("state", "")).lower()
            if state == "done":
                return result
            if state == "error":
                raise ApiError(str(result.get("error") or "YouTube audio import failed."))
            time.sleep(2.0)

        raise ApiError("YouTube audio import took longer than 30 minutes and was stopped in the manager.")

    def upload_hymn_audio(self, file_path: str | Path) -> dict[str, Any]:
        return self._request_file(
            "/api/content/audio/upload",
            "audio",
            file_path,
            timeout=900,
        )

    def delete_unpublished_audio(self, audio_file: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/content/audio/delete-unpublished",
            {"audio_file": audio_file},
            timeout=30,
        )

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health", auth=False, timeout=5)

    def login(self, username: str, password: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            "/api/content/login",
            {"username": username, "password": password},
            auth=False,
        )
        self.token = result.get("token", "")
        self.user = result.get("user") or {}
        if not self.token:
            raise ApiError("The website did not return a login session.")
        return result

    def logout(self) -> None:
        self.token = ""
        self.user = None

    def current(self) -> dict[str, Any]:
        return self._request("GET", "/api/content/current")

    def validate(self, content: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/content/validate", {"content": content})

    def publish(self, content: dict[str, Any], github_backup: bool, base_revision: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/content/publish",
            {"content": content, "github_backup": github_backup, "base_revision": base_revision},
        )

    def redeploy(self) -> dict[str, Any]:
        return self._request("POST", "/api/content/redeploy", {})

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/api/content/status")


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "item"


def next_sort(items: list[dict[str, Any]]) -> int:
    if not items:
        return 10
    return max(int(item.get("sort", 0) or 0) for item in items) + 10


def resequence(items: list[dict[str, Any]]) -> None:
    for index, item in enumerate(items, start=1):
        item["sort"] = index * 10


def managed_audio_files(content: dict[str, Any] | None) -> set[str]:
    files: set[str] = set()
    for level in (content or {}).get("levels", []) or []:
        for year in level.get("years", []) or []:
            for hymn in year.get("hymns", []) or []:
                for recording in hymn.get("recordings", []) or []:
                    if str(recording.get("type", "soundcloud") or "soundcloud").lower() != "audio":
                        continue
                    name = str(recording.get("audio_file", "")).strip()
                    if name:
                        files.add(name)
    return files


def validate_time_text(value: str, *, field_name: str = "time") -> str:
    """Validate a hymn/recording timestamp and return the cleaned text."""
    text = str(value or "").strip() or "0:00"
    try:
        parts = text.split(":")
        if len(parts) == 1:
            seconds = float(parts[0])
            if seconds < 0:
                raise ValueError
        elif len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            if minutes < 0 or seconds < 0 or seconds >= 60:
                raise ValueError
        elif len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            if hours < 0 or minutes < 0 or minutes >= 60 or seconds < 0 or seconds >= 60:
                raise ValueError
        else:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Enter the {field_name} as 0:00, 1:23, 1:23.5, or 1:02:03."
        ) from exc
    return text


def validate_optional_time_text(value: str, *, field_name: str = "time") -> str:
    """Validate an optional timestamp. Blank means no limit."""
    text = str(value or "").strip()
    if not text:
        return ""
    return validate_time_text(text, field_name=field_name)


def time_text_to_ms(value: str) -> int:
    """Convert an already validated timestamp to milliseconds."""
    text = str(value or "").strip() or "0:00"
    parts = text.split(":")
    seconds = float(parts[-1])
    if len(parts) == 1:
        total = seconds
    elif len(parts) == 2:
        total = int(parts[0]) * 60 + seconds
    else:
        total = int(parts[0]) * 3600 + int(parts[1]) * 60 + seconds
    return max(0, int(round(total * 1000)))


def split_bulk_lyric_stanzas(text: str) -> list[str]:
    """
    Split pasted hymn text into stanzas.

    A blank line starts a new stanza. Single line breaks inside one stanza are
    treated as source-document wrapping and become a normal space. Characters
    themselves are not translated, corrected, or Unicode-normalized here.
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return []

    blocks = re.split(r"\n[ \t]*\n+", value)
    result: list[str] = []

    for block in blocks:
        lines = [line.strip() for line in block.split("\n")]
        stanza = " ".join(line for line in lines if line)
        stanza = re.sub(r"[ \t]{2,}", " ", stanza).strip()
        if stanza:
            result.append(stanza)

    return result


def default_content() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "site_title": "St. Mina Hymns School",
        "site_subtitle": "St. Mina Coptic Orthodox Church • Calgary, AB",
        "footer_text": "St. Mina Coptic Orthodox Church (Calgary)",
        "languages": [
            {"code": "en", "name": "English", "is_rtl": False, "default_on": True, "sort": 10},
            {"code": "cop", "name": "Coptic", "is_rtl": False, "default_on": True, "sort": 20},
            {"code": "cop_en", "name": "Coptic-English", "is_rtl": False, "default_on": False, "sort": 30},
        ],
        "levels": [],
    }


class BusyDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, message: str):
        super().__init__(parent)
        self.title(APP_NAME)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(bg=CREAM)

        card = tk.Frame(
            self,
            bg=PAPER,
            padx=24,
            pady=20,
            highlightbackground=LINE,
            highlightthickness=1,
        )
        card.pack(fill="both", expand=True)

        tk.Label(
            card,
            text=message,
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 10, "bold"),
        ).pack()

        bar = ttk.Progressbar(
            card,
            mode="indeterminate",
            length=300,
        )
        bar.pack(pady=(14, 0))
        bar.start(12)

        self.update_idletasks()
        x = parent.winfo_rootx() + max(
            0,
            (parent.winfo_width() - self.winfo_width()) // 2,
        )
        y = parent.winfo_rooty() + max(
            0,
            (parent.winfo_height() - self.winfo_height()) // 2,
        )
        self.geometry(f"+{x}+{y}")


class RecordDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str, fields: list[tuple[str, str, str, Any]], initial: dict[str, Any] | None = None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.resizable(True, False)
        self.result: dict[str, Any] | None = None
        self.vars: dict[str, Any] = {}
        initial = initial or {}
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)
        row = 0
        for label, key, kind, default in fields:
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=(0, 4))
            value = initial.get(key, default)
            if kind == "bool":
                var = tk.BooleanVar(value=bool(value))
                widget = ttk.Checkbutton(frame, variable=var)
                widget.grid(row=row, column=1, sticky="w", padx=(12, 0), pady=(0, 8))
            elif kind == "multiline":
                widget = tk.Text(frame, width=55, height=5, wrap="word")
                widget.insert("1.0", str(value or ""))
                widget.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(0, 8))
                var = widget
            else:
                var = tk.StringVar(value=str(value if value is not None else ""))
                widget = ttk.Entry(frame, textvariable=var, width=52)
                widget.grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=(0, 8))
            self.vars[key] = (kind, var)
            row += 1
        frame.columnconfigure(1, weight=1)
        actions = ttk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Save", command=self._save).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda _e: self.destroy())
        self.wait_visibility()
        self.focus_force()
        self.wait_window()

    def _save(self) -> None:
        result: dict[str, Any] = {}
        for key, (kind, var) in self.vars.items():
            if kind == "multiline":
                result[key] = var.get("1.0", "end-1c").strip()
            else:
                result[key] = var.get()
        self.result = result
        self.destroy()



class CopticConverterDialog(tk.Toplevel):
    """
    Unicode Coptic -> Avva Shenouda PREVIEW.

    Important: Unicode is the source of truth. The legacy Avva string is never
    sent back to the lyric editor for storage. This preserves literal brackets,
    punctuation, numbers, and English text unambiguously.
    """

    def __init__(
        self,
        parent: tk.Misc,
        initial_unicode: str = "",
        on_use: Callable[[str], None] | None = None,
    ):
        super().__init__(parent)
        self.title("Coptic Converter")
        self.transient(parent)
        self.grab_set()
        self.geometry("980x620")
        self.minsize(760, 500)
        self.configure(bg=CREAM)
        self.on_use = on_use

        installed = set(tkfont.families())
        self.avva_family = next(
            (
                candidate
                for candidate in (
                    "Avva_Shenouda",
                    "Avva Shenouda",
                    "AVVA_SHENOUDA",
                    "CS Avva Shenouda",
                )
                if candidate in installed
            ),
            None,
        )

        outer = tk.Frame(self, bg=CREAM, padx=20, pady=20)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=BURGUNDY_900, padx=18, pady=14)
        header.pack(fill="x")

        tk.Label(
            header,
            text="Coptic Font Converter",
            bg=BURGUNDY_900,
            fg="white",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")

        tk.Label(
            header,
            text="Unicode Coptic → Avva Shenouda preview",
            bg=BURGUNDY_900,
            fg="#f3cfd7",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(2, 0))

        card = tk.Frame(
            outer,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=16,
            pady=16,
        )
        card.pack(fill="both", expand=True, pady=(14, 0))
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(2, weight=1)
        card.grid_rowconfigure(1, weight=1)

        tk.Label(
            card,
            text="Unicode Coptic — this is what is saved",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 7))

        tk.Label(
            card,
            text="Avva Shenouda preview — display only",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=2, sticky="w", pady=(0, 7))

        self.input_text = tk.Text(
            card,
            wrap="word",
            undo=True,
            font=("Segoe UI", 16),
            padx=12,
            pady=12,
            relief="flat",
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.input_text.grid(row=1, column=0, sticky="nsew")
        self.input_text.insert("1.0", initial_unicode)

        controls = tk.Frame(card, bg=PAPER, padx=12)
        controls.grid(row=1, column=1, sticky="ns")

        ttk.Button(
            controls,
            text="Refresh preview →",
            style="Primary.TButton",
            command=self.convert,
        ).pack(pady=(65, 8), fill="x")

        ttk.Button(
            controls,
            text="Clear",
            style="Quiet.TButton",
            command=self.clear,
        ).pack(pady=8, fill="x")

        self.output_text = tk.Text(
            card,
            wrap="word",
            undo=False,
            font=("Segoe UI", 16),
            padx=12,
            pady=12,
            relief="flat",
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.output_text.grid(row=1, column=2, sticky="nsew")

        self.output_text.tag_configure(
            "avva",
            font=(self.avva_family or "Courier New", 18),
        )
        self.output_text.tag_configure(
            "plain",
            font=("Segoe UI", 16),
        )

        bottom = tk.Frame(card, bg=PAPER)
        bottom.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))

        if self.avva_family:
            status_text = f"Avva preview font detected: {self.avva_family}"
        else:
            status_text = (
                "Avva font is not installed on this computer. Conversion still "
                "works, but the Avva preview will look like legacy Latin codes."
            )

        self.status = tk.Label(
            bottom,
            text=status_text,
            bg=PAPER,
            fg=MUTED,
            font=("Segoe UI", 9),
        )
        self.status.pack(side="left")

        if self.on_use:
            ttk.Button(
                bottom,
                text="Use Unicode text in lyric",
                style="Primary.TButton",
                command=self.use_unicode,
            ).pack(side="right")

        ttk.Button(
            bottom,
            text="Close",
            style="Quiet.TButton",
            command=self.destroy,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Control-Return>", lambda _e: self.convert())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.input_text.bind("<KeyRelease>", lambda _e: self._schedule_preview())

        self._preview_after_id: str | None = None
        self.after(50, self.convert)
        self.input_text.focus_set()

    def _schedule_preview(self) -> None:
        if self._preview_after_id:
            try:
                self.after_cancel(self._preview_after_id)
            except tk.TclError:
                pass
        self._preview_after_id = self.after(180, self.convert)

    def _render_preview(self, source: str) -> None:
        self.output_text.configure(state="normal")
        self.output_text.delete("1.0", "end")

        for kind, value in unicode_coptic_to_avva_runs(source):
            self.output_text.insert("end", value, kind)

        self.output_text.configure(state="disabled")

    def convert(self) -> None:
        self._preview_after_id = None
        source = self.input_text.get("1.0", "end-1c")
        self._render_preview(source)
        self.status.configure(
            text=(
                f"Preview updated from {len(source)} Unicode characters."
                if source
                else "Paste Unicode Coptic on the left."
            )
        )

    def clear(self) -> None:
        self.input_text.delete("1.0", "end")
        self._render_preview("")
        self.input_text.focus_set()

    def use_unicode(self) -> None:
        source = unicodedata.normalize(
            "NFC",
            self.input_text.get("1.0", "end-1c"),
        )
        if not source.strip():
            messagebox.showinfo(
                "Nothing to use",
                "Paste Unicode Coptic first.",
                parent=self,
            )
            return

        if self.on_use:
            self.on_use(source)
            self.destroy()


class LyricDialog(tk.Toplevel):
    """
    Timestamped lyric editor.

    Coptic is STORED AS UNICODE. Avva Shenouda is preview/rendering only.
    This keeps literal (), [], {}, punctuation, numbers, and English text from
    being confused with Avva's legacy ASCII glyph positions.
    """

    def __init__(
        self,
        parent: tk.Misc,
        languages: list[dict[str, Any]],
        initial: dict[str, Any] | None = None,
    ):
        super().__init__(parent)
        self.title("Lyric row")
        self.transient(parent)
        self.grab_set()
        self.geometry("900x720")
        self.minsize(760, 560)
        self.configure(bg=CREAM)

        self.result: dict[str, Any] | None = None
        initial = initial or {}
        self.time_var = tk.StringVar(value=str(initial.get("t", "0:00")))
        self.published_var = tk.BooleanVar(value=bool(initial.get("published", True)))
        self.text_widgets: dict[str, tk.Text] = {}
        self.coptic_unicode_widget: tk.Text | None = None
        self.coptic_avva_widget: tk.Text | None = None
        self._preview_after_id: str | None = None

        outer = tk.Frame(self, bg=CREAM, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        top_card = tk.Frame(
            outer,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=16,
            pady=14,
        )
        top_card.pack(fill="x")

        tk.Label(
            top_card,
            text="Lyric row",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 15, "bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))

        tk.Label(
            top_card,
            text="Timestamp",
            bg=PAPER,
            fg=INK,
            font=("Segoe UI", 10, "bold"),
        ).grid(row=1, column=0, sticky="w")

        ttk.Entry(
            top_card,
            textvariable=self.time_var,
            width=16,
        ).grid(row=1, column=1, sticky="w", padx=(8, 18))

        ttk.Checkbutton(
            top_card,
            text="Published",
            variable=self.published_var,
        ).grid(row=1, column=2, sticky="w")

        tk.Label(
            top_card,
            text="Examples: 0:06.5  •  2:15  •  1:02:03",
            bg=PAPER,
            fg=MUTED,
            font=("Segoe UI", 9),
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(7, 0))

        editor_card = tk.Frame(
            outer,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        editor_card.pack(fill="both", expand=True, pady=(12, 0))

        canvas = tk.Canvas(editor_card, highlightthickness=0, bg=PAPER)
        scroll = ttk.Scrollbar(editor_card, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=PAPER)

        inner.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )

        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(window_id, width=event.width),
        )
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        texts = initial.get("texts") or {}

        for language in languages:
            code = str(language.get("code", "")).strip()
            name = str(language.get("name", code)).strip() or code
            existing = str(texts.get(code, ""))

            section = tk.Frame(
                inner,
                bg=ROSE_50 if code == "cop" else PAPER,
                highlightbackground=LINE,
                highlightthickness=1,
                padx=12,
                pady=10,
            )
            section.pack(fill="x", pady=(0, 10))

            heading = tk.Frame(section, bg=section["bg"])
            heading.pack(fill="x")

            tk.Label(
                heading,
                text=f"{name} ({code})",
                bg=section["bg"],
                fg=BURGUNDY_950,
                font=("Segoe UI", 10, "bold"),
            ).pack(side="left")

            if code == "cop":
                ttk.Button(
                    heading,
                    text="Open full Coptic converter",
                    style="Quiet.TButton",
                    command=self._open_converter,
                ).pack(side="right")

                tk.Label(
                    section,
                    text="Unicode Coptic — this is the value saved to the website",
                    bg=section["bg"],
                    fg=MUTED,
                    font=("Segoe UI", 9),
                ).pack(anchor="w", pady=(8, 4))

                unicode_box = tk.Text(
                    section,
                    height=5,
                    wrap="word",
                    font=("Segoe UI", 14),
                    padx=10,
                    pady=9,
                    relief="flat",
                    highlightbackground=LINE,
                    highlightthickness=1,
                )
                unicode_box.pack(fill="x")

                # Old rows may still be legacy Avva text. Convert those once for
                # editing. New Unicode rows are left exactly as Unicode.
                editable_unicode = avva_to_unicode_coptic(existing) if existing else ""
                unicode_box.insert("1.0", editable_unicode)

                self.coptic_unicode_widget = unicode_box
                self.text_widgets[code] = unicode_box

                convert_row = tk.Frame(section, bg=section["bg"])
                convert_row.pack(fill="x", pady=7)

                ttk.Button(
                    convert_row,
                    text="Refresh Avva preview",
                    style="Primary.TButton",
                    command=self._convert_coptic_inline,
                ).pack(side="left")

                tk.Label(
                    convert_row,
                    text=(
                        "Preview only. Unicode is saved, so normal brackets and "
                        "punctuation stay normal."
                    ),
                    bg=section["bg"],
                    fg=MUTED,
                    font=("Segoe UI", 9),
                ).pack(side="left", padx=(10, 0))

                installed = set(tkfont.families())
                avva_family = next(
                    (
                        candidate
                        for candidate in (
                            "Avva_Shenouda",
                            "Avva Shenouda",
                            "AVVA_SHENOUDA",
                            "CS Avva Shenouda",
                        )
                        if candidate in installed
                    ),
                    None,
                )

                tk.Label(
                    section,
                    text="Avva Shenouda preview",
                    bg=section["bg"],
                    fg=MUTED,
                    font=("Segoe UI", 9),
                ).pack(anchor="w", pady=(2, 4))

                avva_box = tk.Text(
                    section,
                    height=5,
                    wrap="word",
                    font=("Segoe UI", 16),
                    padx=10,
                    pady=9,
                    relief="flat",
                    highlightbackground=LINE,
                    highlightthickness=1,
                )
                avva_box.pack(fill="x")
                avva_box.tag_configure(
                    "avva",
                    font=(avva_family or "Courier New", 16),
                )
                avva_box.tag_configure(
                    "plain",
                    font=("Segoe UI", 16),
                )
                self.coptic_avva_widget = avva_box

                unicode_box.bind(
                    "<KeyRelease>",
                    lambda _e: self._schedule_coptic_preview(),
                )
                self.after(50, self._convert_coptic_inline)

            else:
                text = tk.Text(
                    section,
                    height=5,
                    wrap="word",
                    font=("Segoe UI", 12),
                    padx=10,
                    pady=9,
                    relief="flat",
                    highlightbackground=LINE,
                    highlightthickness=1,
                )
                text.pack(fill="x", pady=(8, 0))
                text.insert("1.0", existing)
                self.text_widgets[code] = text

        actions = tk.Frame(outer, bg=CREAM)
        actions.pack(fill="x", pady=(12, 0))

        ttk.Button(
            actions,
            text="Cancel",
            style="Quiet.TButton",
            command=self.destroy,
        ).pack(side="right")

        ttk.Button(
            actions,
            text="Save lyric row",
            style="Primary.TButton",
            command=self._save,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.wait_visibility()
        self.focus_force()
        self.wait_window()

    def _schedule_coptic_preview(self) -> None:
        if self._preview_after_id:
            try:
                self.after_cancel(self._preview_after_id)
            except tk.TclError:
                pass
        self._preview_after_id = self.after(180, self._convert_coptic_inline)

    def _convert_coptic_inline(self) -> None:
        self._preview_after_id = None
        if not self.coptic_unicode_widget:
            return

        source = self.coptic_unicode_widget.get("1.0", "end-1c")
        self._render_coptic_preview(source)

    def _render_coptic_preview(self, source: str) -> None:
        if not self.coptic_avva_widget:
            return

        box = self.coptic_avva_widget
        box.configure(state="normal")
        box.delete("1.0", "end")

        for kind, value in unicode_coptic_to_avva_runs(source):
            box.insert("end", value, kind)

        box.configure(state="disabled")

    def _open_converter(self) -> None:
        unicode_text = (
            self.coptic_unicode_widget.get("1.0", "end-1c")
            if self.coptic_unicode_widget
            else ""
        )

        def use_unicode(text: str) -> None:
            if not self.coptic_unicode_widget:
                return
            self.coptic_unicode_widget.delete("1.0", "end")
            self.coptic_unicode_widget.insert("1.0", text)
            self._render_coptic_preview(text)

        CopticConverterDialog(
            self,
            initial_unicode=unicode_text,
            on_use=use_unicode,
        )

    def _save(self) -> None:
        timestamp = self.time_var.get().strip()
        if not timestamp:
            messagebox.showerror(
                "Missing timestamp",
                "Enter a timestamp.",
                parent=self,
            )
            return

        texts: dict[str, str] = {}

        for code, widget in self.text_widgets.items():
            value = widget.get("1.0", "end-1c").strip()

            if code == "cop":
                # Unicode Coptic is the source of truth from v3.3 onward.
                value = unicodedata.normalize("NFC", value)

            if value:
                texts[code] = value

        self.result = {
            "t": timestamp,
            "texts": texts,
            "published": bool(self.published_var.get()),
        }
        self.destroy()


class BulkLyricImportDialog(tk.Toplevel):
    """
    Paste full hymn text in multiple languages and turn it into lyric rows.

    Equal stanza counts are matched deterministically by position. If counts
    differ, the user is asked to correct the stanza breaks before importing.
    English screenshots can be sent to the authenticated website for free,
    local Tesseract OCR. The original extracted/pasted text remains editable.
    """

    def __init__(
        self,
        parent: "ContentManagerApp",
        client: ContentApiClient,
        hymn_title: str,
        languages: list[dict[str, Any]],
    ):
        super().__init__(parent)
        self.title("Bulk lyric importer")
        self.transient(parent)
        self.grab_set()
        self.geometry("1180x790")
        self.minsize(920, 650)
        self.configure(bg=CREAM)

        self.parent_app = parent
        self.client = client
        self.hymn_title = hymn_title
        self.languages = [
            {
                "code": str(item.get("code", "")).strip(),
                "name": str(item.get("name", item.get("code", ""))).strip(),
            }
            for item in languages
            if str(item.get("code", "")).strip()
        ]
        self.input_widgets: dict[str, tk.Text] = {}
        self.input_tabs: dict[str, tk.Misc] = {}
        self.parsed_stanzas: dict[str, list[str]] = {}
        self.preview_rows: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.mode_var = tk.StringVar(value="replace")
        self.status_var = tk.StringVar(
            value="Paste lyrics into the language tabs, then choose Parse & Align Lyrics."
        )

        outer = tk.Frame(self, bg=CREAM, padx=18, pady=18)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        header = tk.Frame(outer, bg=BURGUNDY_900, padx=18, pady=14)
        header.grid(row=0, column=0, sticky="ew")

        tk.Label(
            header,
            text="Bulk Lyric Importer",
            bg=BURGUNDY_900,
            fg="white",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")

        tk.Label(
            header,
            text=f"{hymn_title or 'Selected hymn'}  •  original text is preserved",
            bg=BURGUNDY_900,
            fg="#f3cfd7",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        body = tk.PanedWindow(
            outer,
            orient="horizontal",
            sashwidth=6,
            bg=CREAM,
            bd=0,
            relief="flat",
        )
        body.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

        input_card = tk.Frame(
            body,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        preview_card = tk.Frame(
            body,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        body.add(input_card, minsize=410, stretch="always")
        body.add(preview_card, minsize=430, stretch="always")

        tk.Label(
            input_card,
            text="1. Paste the full hymn",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        tk.Label(
            input_card,
            text=(
                "Blank line = next stanza. A single line break inside a stanza is treated as "
                "word wrapping and becomes a space. For English screenshots, use OCR English image(s)."
            ),
            bg=PAPER,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=500,
            justify="left",
        ).pack(anchor="w", pady=(3, 8))

        input_notebook = ttk.Notebook(input_card)
        input_notebook.pack(fill="both", expand=True)
        self.input_notebook = input_notebook

        for language in self.languages:
            code = language["code"]
            name = language["name"] or code
            tab = ttk.Frame(input_notebook, padding=8)
            input_notebook.add(tab, text=name)
            self.input_tabs[code] = tab

            text = tk.Text(
                tab,
                wrap="word",
                undo=True,
                font=("Segoe UI", 12 if code != "cop" else 14),
                padx=10,
                pady=10,
                relief="flat",
                highlightbackground=LINE,
                highlightthickness=1,
            )
            scroll = ttk.Scrollbar(tab, orient="vertical", command=text.yview)
            text.configure(yscrollcommand=scroll.set)
            text.grid(row=0, column=0, sticky="nsew")
            scroll.grid(row=0, column=1, sticky="ns")
            tab.rowconfigure(0, weight=1)
            tab.columnconfigure(0, weight=1)
            self.input_widgets[code] = text

        parse_row = tk.Frame(input_card, bg=PAPER)
        # Pack this before the expanding notebook so the action buttons can
        # never be pushed out of view on smaller displays or high DPI scaling.
        parse_row.pack(fill="x", pady=(0, 10), before=input_notebook)

        self.parse_button = ttk.Button(
            parse_row,
            text="Parse & Align Lyrics",
            style="Primary.TButton",
            command=self.parse_and_align,
        )
        self.parse_button.pack(side="left")

        ttk.Button(
            parse_row,
            text="Clear all",
            style="Quiet.TButton",
            command=self.clear_inputs,
        ).pack(side="left", padx=(8, 0))

        if "en" in self.input_widgets:
            ttk.Button(
                parse_row,
                text="OCR English image(s)…",
                style="Quiet.TButton",
                command=self.extract_english_from_images,
            ).pack(side="left", padx=(8, 0))

        tk.Label(
            preview_card,
            text="2. Review the generated rows",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w")

        self.count_label = tk.Label(
            preview_card,
            text="No lyrics parsed yet.",
            bg=PAPER,
            fg=MUTED,
            font=("Segoe UI", 9),
            justify="left",
        )
        self.count_label.pack(anchor="w", pady=(3, 8))

        table_frame = tk.Frame(preview_card, bg=PAPER)
        table_frame.pack(fill="both", expand=True)
        columns = ["row"] + [item["code"] for item in self.languages] + ["confidence"]
        self.preview_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self.preview_tree.heading("row", text="#")
        self.preview_tree.column("row", width=45, stretch=False, anchor="center")
        for language in self.languages:
            code = language["code"]
            self.preview_tree.heading(code, text=language["name"] or code)
            self.preview_tree.column(code, width=210, stretch=True)
        self.preview_tree.heading("confidence", text="Match")
        self.preview_tree.column("confidence", width=95, stretch=False, anchor="center")

        preview_scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.preview_tree.yview)
        preview_scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.preview_tree.xview)
        self.preview_tree.configure(
            yscrollcommand=preview_scroll_y.set,
            xscrollcommand=preview_scroll_x.set,
        )
        self.preview_tree.grid(row=0, column=0, sticky="nsew")
        preview_scroll_y.grid(row=0, column=1, sticky="ns")
        preview_scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.note_box = tk.Text(
            preview_card,
            height=5,
            wrap="word",
            state="disabled",
            font=("Segoe UI", 9),
            padx=9,
            pady=8,
            relief="flat",
            highlightbackground=LINE,
            highlightthickness=1,
        )
        # Keep the alignment note visible above the expanding preview table.
        self.note_box.pack(fill="x", pady=(0, 8), before=table_frame)
        self.preview_tree.bind("<<TreeviewSelect>>", lambda _e: self.show_selected_note())

        footer = tk.Frame(outer, bg=CREAM)
        # Fixed grid row: this footer contains "Import lyric rows" and must
        # remain visible regardless of how much space the panes request.
        footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))

        mode_box = tk.Frame(footer, bg=CREAM)
        mode_box.pack(side="left")
        tk.Label(
            mode_box,
            text="Import mode:",
            bg=CREAM,
            fg=INK,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        ttk.Radiobutton(
            mode_box,
            text="Replace existing lyrics",
            variable=self.mode_var,
            value="replace",
        ).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(
            mode_box,
            text="Append",
            variable=self.mode_var,
            value="append",
        ).pack(side="left", padx=(8, 0))

        actions = tk.Frame(footer, bg=CREAM)
        actions.pack(side="right")
        ttk.Button(
            actions,
            text="Cancel",
            style="Quiet.TButton",
            command=self.destroy,
        ).pack(side="right")

        self.import_button = ttk.Button(
            actions,
            text="Import lyric rows",
            style="Primary.TButton",
            command=self.finish_import,
            state="disabled",
        )
        self.import_button.pack(side="right", padx=(0, 8))

        tk.Label(
            outer,
            textvariable=self.status_var,
            bg=CREAM,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=1080,
            justify="left",
        ).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.wait_visibility()
        self.focus_force()
        self.wait_window()

    def extract_english_from_images(self) -> None:
        english_widget = self.input_widgets.get("en")
        if english_widget is None:
            messagebox.showinfo(
                "English OCR",
                "This curriculum does not currently have an English language field.",
                parent=self,
            )
            return

        paths = filedialog.askopenfilenames(
            parent=self,
            title="Choose English hymn screenshot(s)",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
                ("PNG images", "*.png"),
                ("JPEG images", "*.jpg *.jpeg"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return

        current_text = english_widget.get("1.0", "end-1c").strip()
        append_to_existing = False
        if current_text:
            choice = messagebox.askyesnocancel(
                "English OCR",
                (
                    "The English box already contains text.\n\n"
                    "Yes = append the OCR text after the existing text.\n"
                    "No = replace the existing English text.\n"
                    "Cancel = do nothing."
                ),
                parent=self,
            )
            if choice is None:
                return
            append_to_existing = bool(choice)

        selected_paths = [str(value) for value in paths]

        def work() -> list[dict[str, Any]]:
            return [self.client.ocr_english_image(path) for path in selected_paths]

        def done(results: list[dict[str, Any]]) -> None:
            extracted_parts = [str(item.get("text", "")).strip() for item in results]
            extracted_parts = [value for value in extracted_parts if value]
            if not extracted_parts:
                messagebox.showwarning(
                    "English OCR",
                    "No readable English text was found in the selected images.",
                    parent=self,
                )
                return

            extracted = "\n\n".join(extracted_parts)
            if append_to_existing and current_text:
                final_text = current_text.rstrip() + "\n\n" + extracted
            else:
                final_text = extracted

            english_widget.delete("1.0", "end")
            english_widget.insert("1.0", final_text)
            self.input_notebook.select(self.input_tabs["en"])

            # Any old preview no longer represents the edited source text.
            self.parsed_stanzas = {}
            self.preview_rows = []
            self.refresh_preview()

            stanza_count = sum(int(item.get("stanzas", 0) or 0) for item in results)
            self.status_var.set(
                f"OCR extracted about {stanza_count} English stanza(s) from "
                f"{len(results)} image(s). Review the English text, then choose Parse & Align Lyrics."
            )
            english_widget.focus_set()

        self.parent_app.run_async(
            f"Extracting English text from {len(selected_paths)} image(s)…",
            work,
            done,
        )

    def clear_inputs(self) -> None:
        for widget in self.input_widgets.values():
            widget.delete("1.0", "end")
        self.parsed_stanzas = {}
        self.preview_rows = []
        self.refresh_preview()
        self.status_var.set("Paste lyrics into the language tabs, then choose Parse & Align Lyrics.")

    def collect_stanzas(self) -> dict[str, list[str]]:
        parsed: dict[str, list[str]] = {}
        for language in self.languages:
            code = language["code"]
            widget = self.input_widgets[code]
            parsed[code] = split_bulk_lyric_stanzas(widget.get("1.0", "end-1c"))
        return parsed

    def parse_and_align(self) -> None:
        self.parsed_stanzas = self.collect_stanzas()
        nonzero_counts = [len(items) for items in self.parsed_stanzas.values() if items]
        self.update_count_label()

        if not nonzero_counts:
            messagebox.showinfo(
                "No lyrics",
                "Paste lyrics into at least one language tab first.",
                parent=self,
            )
            return

        if len(nonzero_counts) == 1:
            row_count = nonzero_counts[0]
            self.preview_rows = self.build_position_rows(row_count)
            self.status_var.set(
                "Only one language contains text, so rows were created directly without AI."
            )
            self.refresh_preview()
            return

        if len(set(nonzero_counts)) == 1:
            row_count = nonzero_counts[0]
            self.preview_rows = self.build_position_rows(row_count)
            self.status_var.set(
                f"All provided languages contain {row_count} stanzas. Matched by position; no AI request was needed."
            )
            self.refresh_preview()
            return

        messagebox.showwarning(
            "Stanza counts do not match",
            (
                "The pasted languages contain different numbers of stanzas.\n\n"
                "Automatic import is only available when the stanza counts match.\n\n"
                "Review the blank-line separation in each language and make sure "
                "corresponding hymn verses are separated consistently."
            ),
            parent=self,
        )
        self.status_var.set(
            "Stanza counts do not match. Adjust the stanza breaks and try again."
        )
        self.preview_rows = []
        self.refresh_preview()

    def build_position_rows(self, row_count: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index in range(row_count):
            mapping: dict[str, list[int]] = {}
            for language in self.languages:
                code = language["code"]
                mapping[code] = [index + 1] if index < len(self.parsed_stanzas.get(code, [])) else []
            rows.append(
                {
                    "mapping": mapping,
                    "confidence": 1.0,
                    "note": "Matched by stanza position.",
                    "source": "position",
                }
            )
        return rows

    def update_count_label(self) -> None:
        parts: list[str] = []
        for language in self.languages:
            code = language["code"]
            parts.append(f"{language['name'] or code}: {len(self.parsed_stanzas.get(code, []))}")
        self.count_label.configure(text="  •  ".join(parts) if parts else "No languages configured.")

    def text_for_indexes(self, code: str, indexes: list[int]) -> str:
        source = self.parsed_stanzas.get(code, [])
        values = [source[index - 1] for index in indexes if 1 <= index <= len(source)]
        return "\n".join(values)

    def refresh_preview(self) -> None:
        self.preview_tree.delete(*self.preview_tree.get_children())
        self.update_count_label()

        for row_number, row in enumerate(self.preview_rows, start=1):
            values: list[str] = [str(row_number)]
            mapping = row.get("mapping") or {}
            for language in self.languages:
                code = language["code"]
                text = self.text_for_indexes(code, mapping.get(code, []))
                snippet = text.replace("\n", " / ")
                values.append(snippet[:150])
            confidence = float(row.get("confidence", 0.0) or 0.0)
            values.append(f"{round(confidence * 100)}%")
            self.preview_tree.insert("", "end", iid=str(row_number - 1), values=values)

        self.import_button.configure(state="normal" if self.preview_rows else "disabled")
        self.show_selected_note()

    def show_selected_note(self) -> None:
        selected = self.preview_tree.selection()
        text = ""
        if selected:
            try:
                row = self.preview_rows[int(selected[0])]
                note = str(row.get("note", "")).strip()
                text = str(row.get("note", "")).strip()
            except (ValueError, IndexError):
                text = ""
        elif self.preview_rows:
            text = "Select a row to see its alignment note."

        self.note_box.configure(state="normal")
        self.note_box.delete("1.0", "end")
        self.note_box.insert("1.0", text)
        self.note_box.configure(state="disabled")

    def finish_import(self) -> None:
        if not self.preview_rows:
            return

        segments: list[dict[str, Any]] = []
        for row_number, row in enumerate(self.preview_rows, start=1):
            texts: dict[str, str] = {}
            mapping = row.get("mapping") or {}
            for language in self.languages:
                code = language["code"]
                value = self.text_for_indexes(code, mapping.get(code, [])).strip()
                if value:
                    texts[code] = value

            segments.append(
                {
                    "t": "0:00",
                    "texts": texts,
                    "sort": row_number * 10,
                    "published": True,
                }
            )

        self.result = {
            "mode": self.mode_var.get(),
            "segments": segments,
        }
        self.destroy()


class CopyHymnDialog(tk.Toplevel):
    """Choose one or more destination years for a full hymn copy."""

    def __init__(
        self,
        parent: tk.Misc,
        content: dict[str, Any],
        source_ref: tuple[str, int | None, int | None, int | None],
        hymn: dict[str, Any],
    ):
        super().__init__(parent)
        self.title("Copy hymn to other years")
        self.transient(parent)
        self.grab_set()
        self.geometry("640x620")
        self.minsize(540, 460)
        self.configure(bg=CREAM)

        self.result: list[tuple[int, int]] | None = None
        self.destination_vars: dict[tuple[int, int], tk.BooleanVar] = {}

        _kind, source_li, source_yi, _source_hi = source_ref
        self.source_li = int(source_li) if source_li is not None else -1
        self.source_yi = int(source_yi) if source_yi is not None else -1

        outer = tk.Frame(self, bg=CREAM, padx=18, pady=18)
        outer.pack(fill="both", expand=True)

        card = tk.Frame(
            outer,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=16,
            pady=16,
        )
        card.pack(fill="both", expand=True)

        tk.Label(
            card,
            text=f"Copy {hymn.get('title') or hymn.get('slug') or 'selected hymn'}",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")

        tk.Label(
            card,
            text=(
                "Choose every destination year. Lyrics, timestamps, notes, "
                "publication state and SoundCloud recordings are copied."
            ),
            bg=PAPER,
            fg=MUTED,
            wraplength=570,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        buttons = tk.Frame(card, bg=PAPER)
        buttons.pack(fill="x", pady=(0, 8))
        ttk.Button(buttons, text="Select all", style="Quiet.TButton", command=self._select_all).pack(side="left")
        ttk.Button(buttons, text="Clear all", style="Quiet.TButton", command=self._clear_all).pack(side="left", padx=(6, 0))

        list_frame = tk.Frame(card, bg=PAPER)
        list_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(list_frame, highlightthickness=0, bg=PAPER)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=PAPER)
        inner.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for li, level in enumerate(content.get("levels", [])):
            tk.Label(
                inner,
                text=level.get("name") or level.get("slug") or f"Level {li + 1}",
                bg=PAPER,
                fg=BURGUNDY_800,
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w", pady=(10 if li else 2, 3))

            for yi, year in enumerate(level.get("years", [])):
                name = year.get("name") or year.get("slug") or f"Year {yi + 1}"

                if li == self.source_li and yi == self.source_yi:
                    tk.Label(
                        inner,
                        text=f"✓ {name} (current year)",
                        bg=PAPER,
                        fg=MUTED,
                    ).pack(anchor="w", padx=(18, 0), pady=2)
                    continue

                var = tk.BooleanVar(value=False)
                self.destination_vars[(li, yi)] = var
                ttk.Checkbutton(inner, text=name, variable=var).pack(
                    anchor="w",
                    padx=(18, 0),
                    pady=2,
                )

        actions = tk.Frame(card, bg=PAPER)
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(actions, text="Cancel", style="Quiet.TButton", command=self.destroy).pack(side="right")
        ttk.Button(
            actions,
            text="Copy to selected years",
            style="Primary.TButton",
            command=self._save,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _event: self.destroy())
        self.wait_visibility()
        self.focus_force()
        self.wait_window()

    def _select_all(self) -> None:
        for var in self.destination_vars.values():
            var.set(True)

    def _clear_all(self) -> None:
        for var in self.destination_vars.values():
            var.set(False)

    def _save(self) -> None:
        selected = [ref for ref, var in self.destination_vars.items() if bool(var.get())]
        if not selected:
            messagebox.showinfo(
                "Choose a destination",
                "Select at least one destination year.",
                parent=self,
            )
            return
        self.result = selected
        self.destroy()


class ContentManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1280x800")
        self.minsize(1020, 680)
        self.client: ContentApiClient | None = None
        self.content: dict[str, Any] = default_content()
        self.remote_status: dict[str, Any] = {}
        self.remote_revision = ""
        self.tree_map: dict[str, tuple[str, int | None, int | None, int | None]] = {}
        self.current_ref: tuple[str, int | None, int | None, int | None] | None = None
        self.dirty = False
        self.configure(bg=CREAM)
        self._load_settings()
        self._configure_style()
        self.brand_logo: tk.PhotoImage | None = None
        logo_path = find_logo_path()
        if logo_path:
            try:
                self.brand_logo = load_scaled_png(logo_path, 58)
                self.iconphoto(True, self.brand_logo)
            except tk.TclError:
                self.brand_logo = None
        self.show_login()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        base_font = ("Segoe UI", 10)
        style.configure(".", font=base_font, background=CREAM, foreground=INK)
        style.configure("TFrame", background=CREAM)
        style.configure("Card.TFrame", background=PAPER)
        style.configure("TLabel", background=CREAM, foreground=INK)
        style.configure(
            "Title.TLabel",
            background=CREAM,
            foreground=BURGUNDY_950,
            font=("Segoe UI", 18, "bold"),
        )
        style.configure(
            "Heading.TLabel",
            background=CREAM,
            foreground=BURGUNDY_950,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "CardHeading.TLabel",
            background=PAPER,
            foreground=BURGUNDY_950,
            font=("Segoe UI", 11, "bold"),
        )
        style.configure(
            "Muted.TLabel",
            background=CREAM,
            foreground=MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "CardMuted.TLabel",
            background=PAPER,
            foreground=MUTED,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Primary.TButton",
            background=BURGUNDY_700,
            foreground="white",
            bordercolor=BURGUNDY_700,
            focusthickness=0,
            padding=(12, 8),
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", BURGUNDY_600), ("pressed", BURGUNDY_900)],
            bordercolor=[("active", BURGUNDY_600)],
            foreground=[("disabled", "#eadde1"), ("!disabled", "white")],
        )
        style.configure(
            "Quiet.TButton",
            background=PAPER,
            foreground=BURGUNDY_800,
            bordercolor="#ddcbd0",
            focusthickness=0,
            padding=(10, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Quiet.TButton",
            background=[("active", ROSE_50)],
            bordercolor=[("active", "#cda8b3")],
        )
        style.configure(
            "Danger.TButton",
            background="#fff4f5",
            foreground=DANGER,
            bordercolor="#efc5cc",
            focusthickness=0,
            padding=(10, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Danger.TButton",
            background=[("active", "#ffe8eb")],
        )
        style.configure(
            "TEntry",
            fieldbackground=PAPER,
            foreground=INK,
            bordercolor="#d9cbd0",
            lightcolor="#d9cbd0",
            darkcolor="#d9cbd0",
            padding=(9, 8),
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", BURGUNDY_600)],
            lightcolor=[("focus", BURGUNDY_600)],
            darkcolor=[("focus", BURGUNDY_600)],
        )
        style.configure(
            "TCheckbutton",
            background=CREAM,
            foreground=INK,
        )
        style.map(
            "TCheckbutton",
            background=[("active", CREAM)],
        )
        style.configure(
            "Treeview",
            background=PAPER,
            fieldbackground=PAPER,
            foreground=INK,
            bordercolor=LINE,
            rowheight=30,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Treeview.Heading",
            background=ROSE_50,
            foreground=BURGUNDY_900,
            bordercolor=LINE,
            font=("Segoe UI", 9, "bold"),
            padding=(8, 8),
        )
        style.map(
            "Treeview",
            background=[("selected", BURGUNDY_700)],
            foreground=[("selected", "white")],
        )
        style.configure(
            "TNotebook",
            background=CREAM,
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=ROSE_50,
            foreground=BURGUNDY_800,
            padding=(14, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", PAPER), ("active", ROSE_100)],
            foreground=[("selected", BURGUNDY_950)],
        )
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=ROSE_100,
            background=BURGUNDY_700,
        )

    def _load_settings(self) -> None:
        self.saved_url = DEFAULT_SITE_URL
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            self.saved_url = str(data.get("site_url") or DEFAULT_SITE_URL)
        except Exception:
            pass

    def _save_settings(self) -> None:
        try:
            SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({"site_url": self.saved_url}, indent=2), encoding="utf-8")
        except OSError:
            pass

    def clear_window(self) -> None:
        for widget in self.winfo_children():
            widget.destroy()

    def show_login(self) -> None:
        self.clear_window()
        self.client = None
        self.configure(bg=CREAM)

        outer = tk.Frame(self, bg=CREAM)
        outer.pack(fill="both", expand=True)

        hero = tk.Frame(outer, bg=BURGUNDY_900, padx=28, pady=20)
        hero.pack(fill="x")

        if self.brand_logo:
            tk.Label(
                hero,
                image=self.brand_logo,
                bg=BURGUNDY_900,
                borderwidth=0,
            ).pack(side="left", padx=(0, 14))

        title_box = tk.Frame(hero, bg=BURGUNDY_900)
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text="St. Mina Hymns School",
            bg=BURGUNDY_900,
            fg="white",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="Content Manager",
            bg=BURGUNDY_900,
            fg="#f3cfd7",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(2, 0))

        center = tk.Frame(outer, bg=CREAM)
        center.pack(fill="both", expand=True)

        card = tk.Frame(
            center,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=30,
            pady=28,
        )
        card.place(relx=0.5, rely=0.46, anchor="center", width=620)

        tk.Label(
            card,
            text="Administrator sign in",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 17, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w")

        tk.Label(
            card,
            text="Sign in with an active Hymns School Administrator account to load and publish curriculum.",
            bg=PAPER,
            fg=MUTED,
            font=("Segoe UI", 9),
            wraplength=540,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 18))

        self.login_url = tk.StringVar(value=self.saved_url)
        self.login_username = tk.StringVar()
        self.login_password = tk.StringVar()

        labels = [
            ("Website", self.login_url, False),
            ("Administrator username", self.login_username, False),
            ("Password", self.login_password, True),
        ]

        username_entry = None

        for row, (label, variable, is_password) in enumerate(labels, start=2):
            tk.Label(
                card,
                text=label,
                bg=PAPER,
                fg=INK,
                font=("Segoe UI", 9, "bold"),
            ).grid(row=row, column=0, sticky="w", pady=7)

            entry = ttk.Entry(
                card,
                textvariable=variable,
                width=44,
                show="•" if is_password else "",
            )
            entry.grid(row=row, column=1, sticky="ew", padx=(14, 0), pady=7)

            if label == "Administrator username":
                username_entry = entry

        self.login_error = tk.Label(
            card,
            text="",
            bg=PAPER,
            fg=DANGER,
            font=("Segoe UI", 9, "bold"),
            wraplength=540,
            justify="left",
        )
        self.login_error.grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Button(
            card,
            text="Sign in and load website",
            style="Primary.TButton",
            command=self.login,
        ).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(18, 0),
        )

        tk.Label(
            card,
            text=(
                "Your password is sent only to your Hymns School website over HTTPS. "
                "The manager does not save it."
            ),
            bg=PAPER,
            fg=MUTED,
            font=("Segoe UI", 8),
            wraplength=540,
            justify="left",
        ).grid(
            row=7,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(12, 0),
        )

        card.columnconfigure(1, weight=1)

        if username_entry:
            username_entry.focus_set()

        self.bind("<Return>", lambda _e: self.login())

    def run_async(
        self,
        message: str,
        work: Callable[[], Any],
        done: Callable[[Any], None],
    ) -> None:
        busy = BusyDialog(self, message)

        def finish_ok(result: Any) -> None:
            try:
                if busy.winfo_exists():
                    busy.destroy()
            except tk.TclError:
                pass

            try:
                done(result)
            except Exception as exc:
                messagebox.showerror(
                    "Content Manager",
                    f"An error occurred while displaying the result:\n\n{exc}",
                    parent=self,
                )

        def finish_error(exc: Exception) -> None:
            try:
                if busy.winfo_exists():
                    busy.destroy()
            except tk.TclError:
                pass

            if isinstance(exc, ApiError) and exc.status == 401 and self.client:
                self.client.logout()

            messagebox.showerror(
                "Content Manager",
                str(exc),
                parent=self,
            )

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                self.after(0, lambda exc=exc: finish_error(exc))
            else:
                self.after(0, lambda result=result: finish_ok(result))

        threading.Thread(
            target=runner,
            daemon=True,
            name="stmina-content-manager-worker",
        ).start()

    def login(self) -> None:
        url = self.login_url.get().strip()
        username = self.login_username.get().strip()
        password = self.login_password.get()
        if not username or not password:
            self.login_error.configure(text="Enter your administrator username and password.")
            return
        try:
            client = ContentApiClient(url)
        except ValueError as exc:
            self.login_error.configure(text=str(exc))
            return

        def work() -> tuple[ContentApiClient, dict[str, Any], dict[str, Any]]:
            login_result = client.login(username, password)
            current = client.current()
            return client, login_result, current

        def done(result: tuple[ContentApiClient, dict[str, Any], dict[str, Any]]) -> None:
            client_obj, _login_result, current = result
            self.client = client_obj
            self.content = current.get("content") or default_content()
            self.remote_status = current.get("status") or {}
            self.remote_revision = str(self.remote_status.get("revision", ""))
            self.saved_url = client_obj.base_url
            self._save_settings()
            self.login_password.set("")
            self.dirty = False
            self.build_editor()

        self.run_async("Signing in and loading website content…", work, done)

    def build_editor(self) -> None:
        self.unbind("<Return>")
        self.clear_window()
        self.configure(bg=CREAM)

        user = self.client.user if self.client else {}

        # Branded header matching the website.
        top = tk.Frame(self, bg=BURGUNDY_900, padx=16, pady=10)
        top.pack(fill="x")

        if self.brand_logo:
            tk.Label(top, image=self.brand_logo, bg=BURGUNDY_900, borderwidth=0).pack(
                side="left",
                padx=(0, 10),
            )

        brand_text = tk.Frame(top, bg=BURGUNDY_900)
        brand_text.pack(side="left")

        tk.Label(
            brand_text,
            text="St. Mina Hymns School",
            bg=BURGUNDY_900,
            fg="white",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")

        tk.Label(
            brand_text,
            text=f"Content Manager  ·  {self.client.base_url if self.client else ''}",
            bg=BURGUNDY_900,
            fg="#f3cfd7",
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(1, 0))

        ttk.Button(
            top,
            text="Sign out",
            style="Quiet.TButton",
            command=self.sign_out,
        ).pack(side="right", padx=(8, 0))

        tk.Label(
            top,
            text=f"Administrator  ·  {user.get('display_name', '')}",
            bg=ROSE_100,
            fg=BURGUNDY_800,
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=5,
        ).pack(side="right")

        toolbar = tk.Frame(
            self,
            bg=PAPER,
            highlightbackground=LINE,
            highlightthickness=1,
            padx=12,
            pady=8,
        )
        toolbar.pack(fill="x", padx=12, pady=(10, 8))

        ttk.Button(
            toolbar,
            text="Refresh website",
            style="Quiet.TButton",
            command=self.refresh_remote,
        ).pack(side="left")

        ttk.Button(
            toolbar,
            text="Open draft…",
            style="Quiet.TButton",
            command=self.open_draft,
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            toolbar,
            text="Save draft…",
            style="Quiet.TButton",
            command=self.save_draft,
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            toolbar,
            text="Coptic Converter",
            style="Primary.TButton",
            command=self.open_coptic_converter,
        ).pack(side="left", padx=(12, 0))

        ttk.Button(
            toolbar,
            text="Export JSON…",
            style="Quiet.TButton",
            command=self.export_json,
        ).pack(side="left", padx=(6, 0))

        self.dirty_label = tk.Label(
            toolbar,
            text="",
            bg=PAPER,
            fg=MUTED,
            font=("Segoe UI", 9, "bold"),
        )
        self.dirty_label.pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        left = ttk.Frame(body, padding=12, style="Card.TFrame")
        right = ttk.Frame(body, padding=12, style="Card.TFrame")
        body.add(left, weight=1)
        body.add(right, weight=3)

        ttk.Label(
            left,
            text="Curriculum",
            style="CardHeading.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        self.curriculum_tree = ttk.Treeview(
            left,
            show="tree",
            selectmode="browse",
        )
        self.curriculum_tree.pack(fill="both", expand=True)
        self.curriculum_tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        tree_buttons = ttk.Frame(left, style="Card.TFrame")
        tree_buttons.pack(fill="x", pady=(8, 0))

        ttk.Button(
            tree_buttons,
            text="+ Level",
            style="Quiet.TButton",
            command=self.add_level,
        ).pack(side="left")

        ttk.Button(
            tree_buttons,
            text="+ Year",
            style="Quiet.TButton",
            command=self.add_year,
        ).pack(side="left", padx=(4, 0))

        ttk.Button(
            tree_buttons,
            text="+ Hymn",
            style="Primary.TButton",
            command=self.add_hymn,
        ).pack(side="left", padx=(4, 0))

        ttk.Button(
            tree_buttons,
            text="Delete",
            style="Danger.TButton",
            command=self.delete_selected,
        ).pack(side="right")

        move_buttons = ttk.Frame(left, style="Card.TFrame")
        move_buttons.pack(fill="x", pady=(6, 0))

        ttk.Button(
            move_buttons,
            text="Move up",
            style="Quiet.TButton",
            command=lambda: self.move_selected(-1),
        ).pack(side="left")

        ttk.Button(
            move_buttons,
            text="Move down",
            style="Quiet.TButton",
            command=lambda: self.move_selected(1),
        ).pack(side="left", padx=(4, 0))

        ttk.Button(
            move_buttons,
            text="Copy hymn…",
            style="Quiet.TButton",
            command=self.copy_selected_hymn,
        ).pack(side="right")

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)
        self.details_tab = ttk.Frame(self.notebook, padding=14)
        self.recordings_tab = ttk.Frame(self.notebook, padding=14)
        self.lyrics_tab = ttk.Frame(self.notebook, padding=14)
        self.languages_tab = ttk.Frame(self.notebook, padding=14)
        self.site_tab = ttk.Frame(self.notebook, padding=14)
        self.publish_tab = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(self.details_tab, text="Selected item")
        self.notebook.add(self.recordings_tab, text="Recordings")
        self.notebook.add(self.lyrics_tab, text="Lyrics")
        self.notebook.add(self.languages_tab, text="Languages")
        self.notebook.add(self.site_tab, text="Site settings")
        self.notebook.add(self.publish_tab, text="Publish")

        self.build_details_tab()
        self.build_recordings_tab()
        self.build_lyrics_tab()
        self.build_languages_tab()
        self.build_site_tab()
        self.build_publish_tab()
        self.rebuild_tree()
        self.refresh_languages()
        self.populate_site_settings()
        self.update_dirty_label()

        status = ttk.Frame(self, padding=(12, 0, 12, 10))
        status.pack(fill="x")
        self.status_label = ttk.Label(status, text="Ready")
        self.status_label.pack(side="left")
        self.summary_label = ttk.Label(status, text=self.content_summary())
        self.summary_label.pack(side="right")

    def sign_out(self) -> None:
        if self.dirty and not messagebox.askyesno("Unsaved changes", "Discard local changes and sign out?", parent=self):
            return
        if self.client:
            self.client.logout()
        self.show_login()

    def mark_dirty(self, value: bool = True) -> None:
        self.dirty = value
        self.update_dirty_label()
        if hasattr(self, "summary_label"):
            self.summary_label.configure(text=self.content_summary())

    def update_dirty_label(self) -> None:
        if hasattr(self, "dirty_label"):
            self.dirty_label.configure(text="● Unpublished changes" if self.dirty else "Saved/published state")

    def content_summary(self) -> str:
        levels = self.content.get("levels", [])
        years = sum(len(level.get("years", [])) for level in levels)
        hymns = sum(len(year.get("hymns", [])) for level in levels for year in level.get("years", []))
        return f"{len(levels)} levels · {years} years · {hymns} hymns"

    def rebuild_tree(self, select_ref: tuple[str, int | None, int | None, int | None] | None = None) -> None:
        self.curriculum_tree.delete(*self.curriculum_tree.get_children())
        self.tree_map.clear()
        to_select = ""
        for li, level in enumerate(self.content.get("levels", [])):
            label = level.get("name") or level.get("slug") or "Unnamed level"
            if not level.get("published", True):
                label += "  [Hidden]"
            iid = self.curriculum_tree.insert("", "end", text=label, open=True)
            ref = ("level", li, None, None)
            self.tree_map[iid] = ref
            if ref == select_ref:
                to_select = iid
            for yi, year in enumerate(level.get("years", [])):
                year_label = year.get("name") or year.get("slug") or "Unnamed year"
                if not year.get("published", True):
                    year_label += "  [Hidden]"
                yid = self.curriculum_tree.insert(iid, "end", text=year_label, open=True)
                yref = ("year", li, yi, None)
                self.tree_map[yid] = yref
                if yref == select_ref:
                    to_select = yid
                for hi, hymn in enumerate(year.get("hymns", [])):
                    hymn_label = hymn.get("title") or hymn.get("slug") or "Unnamed hymn"
                    if not hymn.get("published", True):
                        hymn_label += "  [Hidden]"
                    hid = self.curriculum_tree.insert(yid, "end", text=hymn_label)
                    href = ("hymn", li, yi, hi)
                    self.tree_map[hid] = href
                    if href == select_ref:
                        to_select = hid
        if to_select:
            self.curriculum_tree.selection_set(to_select)
            self.curriculum_tree.see(to_select)
        elif self.curriculum_tree.get_children():
            first = self.curriculum_tree.get_children()[0]
            self.curriculum_tree.selection_set(first)

    def get_ref_object(self, ref: tuple[str, int | None, int | None, int | None] | None = None) -> dict[str, Any] | None:
        ref = ref or self.current_ref
        if not ref:
            return None
        kind, li, yi, hi = ref
        try:
            level = self.content["levels"][int(li)]
            if kind == "level":
                return level
            year = level["years"][int(yi)]
            if kind == "year":
                return year
            return year["hymns"][int(hi)]
        except (IndexError, KeyError, TypeError, ValueError):
            return None

    def selected_ref(self) -> tuple[str, int | None, int | None, int | None] | None:
        selection = self.curriculum_tree.selection()
        if not selection:
            return None
        return self.tree_map.get(selection[0])

    def on_tree_select(self, _event: Any = None) -> None:
        ref = self.selected_ref()
        if not ref:
            return
        self.current_ref = ref
        self.populate_details()
        self.refresh_recordings()
        self.refresh_lyrics()

    def build_details_tab(self) -> None:
        ttk.Label(self.details_tab, text="Selected curriculum item", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.detail_type = tk.StringVar(value="Nothing selected")
        ttk.Label(self.details_tab, textvariable=self.detail_type).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.detail_name = tk.StringVar()
        self.detail_slug = tk.StringVar()
        self.detail_description = tk.StringVar()
        self.detail_published = tk.BooleanVar(value=True)
        labels = [("Name / title", self.detail_name), ("Slug", self.detail_slug), ("Description / note", self.detail_description)]
        for row, (label, variable) in enumerate(labels, start=2):
            ttk.Label(self.details_tab, text=label).grid(row=row, column=0, sticky="w", pady=6)
            ttk.Entry(self.details_tab, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=6)
        ttk.Label(self.details_tab, text="Published").grid(row=5, column=0, sticky="w", pady=6)
        ttk.Checkbutton(self.details_tab, variable=self.detail_published).grid(row=5, column=1, sticky="w", padx=(12, 0), pady=6)
        ttk.Button(self.details_tab, text="Save selected item", command=self.save_details).grid(row=6, column=0, columnspan=2, sticky="e", pady=(14, 0))
        self.details_tab.columnconfigure(1, weight=1)

    def populate_details(self) -> None:
        obj = self.get_ref_object()
        if not obj or not self.current_ref:
            return
        kind = self.current_ref[0]
        self.detail_type.set(kind.title())
        self.detail_name.set(str(obj.get("title") if kind == "hymn" else obj.get("name", "")))
        self.detail_slug.set(str(obj.get("slug", "")))
        self.detail_description.set(str(obj.get("note") if kind == "hymn" else obj.get("description", "")))
        self.detail_published.set(bool(obj.get("published", True)))

    def save_details(self) -> None:
        obj = self.get_ref_object()
        if not obj or not self.current_ref:
            return
        name = self.detail_name.get().strip()
        slug = self.detail_slug.get().strip()
        if not name or not slug:
            messagebox.showerror("Missing information", "Name/title and slug are required.", parent=self)
            return
        kind = self.current_ref[0]
        if kind == "hymn":
            obj["title"] = name
            obj["note"] = self.detail_description.get().strip()
        else:
            obj["name"] = name
            obj["description"] = self.detail_description.get().strip()
        obj["slug"] = slug
        obj["published"] = bool(self.detail_published.get())
        ref = self.current_ref
        self.mark_dirty()
        self.rebuild_tree(ref)

    def add_level(self) -> None:
        name = simpledialog.askstring("Add level", "Level name:", parent=self)
        if not name:
            return
        levels = self.content.setdefault("levels", [])
        level = {
            "slug": slugify(name), "name": name.strip(), "description": "", "sort": next_sort(levels),
            "published": True, "years": []
        }
        levels.append(level)
        self.mark_dirty()
        self.rebuild_tree(("level", len(levels) - 1, None, None))

    def add_year(self) -> None:
        ref = self.selected_ref()
        if not ref:
            messagebox.showinfo("Select a level", "Select a level or an item inside a level first.", parent=self)
            return
        li = ref[1]
        if li is None:
            return
        name = simpledialog.askstring("Add year", "Year name (for example 2026–2027):", parent=self)
        if not name:
            return
        years = self.content["levels"][li].setdefault("years", [])
        year = {
            "slug": slugify(name), "name": name.strip(), "description": "", "sort": next_sort(years),
            "published": True, "hymns": []
        }
        years.append(year)
        self.mark_dirty()
        self.rebuild_tree(("year", li, len(years) - 1, None))

    def add_hymn(self) -> None:
        ref = self.selected_ref()
        if not ref or ref[1] is None:
            messagebox.showinfo("Select a year", "Select a year first.", parent=self)
            return
        li = ref[1]
        yi = ref[2]
        if ref[0] == "level":
            years = self.content["levels"][li].get("years", [])
            if not years:
                messagebox.showinfo("No year", "Add a year to this level first.", parent=self)
                return
            yi = 0
        if yi is None:
            messagebox.showinfo("Select a year", "Select a year first.", parent=self)
            return
        title = simpledialog.askstring("Add hymn", "Hymn title:", parent=self)
        if not title:
            return
        hymns = self.content["levels"][li]["years"][yi].setdefault("hymns", [])
        hymn = {
            "slug": slugify(title), "title": title.strip(), "note": "", "sort": next_sort(hymns),
            "published": True, "recordings": [], "segments": []
        }
        hymns.append(hymn)
        self.mark_dirty()
        self.rebuild_tree(("hymn", li, yi, len(hymns) - 1))

    def open_coptic_converter(self) -> None:
        CopticConverterDialog(self)

    def copy_selected_hymn(self) -> None:
        ref = self.selected_ref()
        hymn = self.get_ref_object(ref)

        if not ref or ref[0] != "hymn" or not hymn:
            messagebox.showinfo(
                "Select a hymn",
                "Select the hymn you want to copy first.",
                parent=self,
            )
            return

        dialog = CopyHymnDialog(self, self.content, ref, hymn)
        if not dialog.result:
            return

        source_title = str(hymn.get("title") or hymn.get("slug") or "Hymn")
        source_slug = str(hymn.get("slug") or "").strip()

        copied_destinations: list[str] = []
        skipped_destinations: list[str] = []
        last_copy_ref: tuple[str, int | None, int | None, int | None] | None = None

        for li, yi in dialog.result:
            try:
                level = self.content["levels"][li]
                year = level["years"][yi]
            except (KeyError, IndexError, TypeError):
                continue

            hymns = year.setdefault("hymns", [])

            duplicate = next(
                (
                    item
                    for item in hymns
                    if str(item.get("slug") or "").strip().casefold()
                    == source_slug.casefold()
                ),
                None,
            )

            destination_name = (
                f"{level.get('name') or level.get('slug') or 'Level'}"
                " → "
                f"{year.get('name') or year.get('slug') or 'Year'}"
            )

            if duplicate is not None:
                skipped_destinations.append(destination_name)
                continue

            copied_hymn = deepcopy(hymn)
            copied_hymn["sort"] = next_sort(hymns)
            hymns.append(copied_hymn)

            copied_destinations.append(destination_name)
            last_copy_ref = ("hymn", li, yi, len(hymns) - 1)

        if copied_destinations:
            self.mark_dirty()
            self.rebuild_tree(last_copy_ref or ref)

        if not copied_destinations:
            messagebox.showwarning(
                "Nothing copied",
                (
                    f"'{source_title}' was not copied.\n\n"
                    "Every selected destination already contains a hymn "
                    "with the same slug."
                ),
                parent=self,
            )
            return

        message = (
            f"Copied '{source_title}' to {len(copied_destinations)} "
            f"{'year' if len(copied_destinations) == 1 else 'years'}."
        )

        if skipped_destinations:
            message += (
                "\n\nSkipped because the hymn already exists there:\n• "
                + "\n• ".join(skipped_destinations)
            )

        messagebox.showinfo(
            "Hymn copied",
            message,
            parent=self,
        )

    def delete_selected(self) -> None:
        ref = self.selected_ref()
        obj = self.get_ref_object(ref)
        if not ref or not obj:
            return
        kind, li, yi, hi = ref
        label = obj.get("title") if kind == "hymn" else obj.get("name")
        if not messagebox.askyesno("Delete", f"Delete {kind} '{label}'?\n\nEverything inside it will also be removed from the curriculum draft.", parent=self):
            return

        audio_before = managed_audio_files(self.content)
        if kind == "level":
            del self.content["levels"][li]
        elif kind == "year":
            del self.content["levels"][li]["years"][yi]
        else:
            del self.content["levels"][li]["years"][yi]["hymns"][hi]
        audio_after = managed_audio_files(self.content)
        draft_only_candidates = sorted(audio_before - audio_after)

        self.current_ref = None
        self.mark_dirty()
        self.rebuild_tree()

        if draft_only_candidates and self.client:
            def work() -> dict[str, Any]:
                assert self.client is not None
                deleted = 0
                for audio_file in draft_only_candidates:
                    result = self.client.delete_unpublished_audio(audio_file)
                    if result.get("deleted"):
                        deleted += 1
                return {"deleted": deleted}

            self.run_async("Checking managed audio files…", work, lambda _result: None)

    def move_selected(self, direction: int) -> None:
        ref = self.selected_ref()
        if not ref:
            return
        kind, li, yi, hi = ref
        if kind == "level":
            items, index = self.content["levels"], li
        elif kind == "year":
            items, index = self.content["levels"][li]["years"], yi
        else:
            items, index = self.content["levels"][li]["years"][yi]["hymns"], hi
        new_index = index + direction
        if new_index < 0 or new_index >= len(items):
            return
        items[index], items[new_index] = items[new_index], items[index]
        resequence(items)
        if kind == "level":
            new_ref = (kind, new_index, None, None)
        elif kind == "year":
            new_ref = (kind, li, new_index, None)
        else:
            new_ref = (kind, li, yi, new_index)
        self.current_ref = new_ref
        self.mark_dirty()
        self.rebuild_tree(new_ref)

    def selected_hymn(self) -> dict[str, Any] | None:
        if not self.current_ref or self.current_ref[0] != "hymn":
            return None
        return self.get_ref_object()

    def build_recordings_tab(self) -> None:
        # Keep the table flexible while pinning all recording actions below it.
        self.recordings_tab.columnconfigure(0, weight=1)
        self.recordings_tab.rowconfigure(2, weight=1)

        ttk.Label(
            self.recordings_tab,
            text="Recordings",
            style="Heading.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            self.recordings_tab,
            text=(
                "Use SoundCloud, import authorized audio from YouTube, or upload an audio file. "
                "Imported/uploaded audio is stored on your Raspberry Pi and uses the site's waveform player."
            ),
            wraplength=760,
        ).grid(row=1, column=0, sticky="w", pady=(3, 8))

        table_frame = ttk.Frame(self.recordings_tab)
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        self.recordings_tree = ttk.Treeview(
            table_frame,
            columns=("type", "label", "source", "start_at", "end_at", "published"),
            show="headings",
            height=9,
        )
        columns = [
            ("type", "Type", 110, False),
            ("label", "Label", 170, False),
            ("source", "Source", 390, True),
            ("start_at", "Start at", 85, False),
            ("end_at", "End at", 85, False),
            ("published", "Published", 85, False),
        ]
        for col, title, width, stretch in columns:
            self.recordings_tree.heading(col, text=title)
            self.recordings_tree.column(col, width=width, minwidth=65, stretch=stretch)

        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.recordings_tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.recordings_tree.xview)
        self.recordings_tree.configure(
            yscrollcommand=scroll_y.set,
            xscrollcommand=scroll_x.set,
        )
        self.recordings_tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")

        # Two compact rows prevent controls from disappearing/overflowing on smaller windows.
        add_controls = ttk.Frame(self.recordings_tab)
        add_controls.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(add_controls, text="Add SoundCloud", command=self.add_recording).pack(side="left")
        ttk.Button(add_controls, text="Import YouTube audio", command=self.import_youtube_recording).pack(
            side="left", padx=(5, 0)
        )
        ttk.Button(add_controls, text="Upload audio file", command=self.upload_audio_recording).pack(
            side="left", padx=(5, 0)
        )

        edit_controls = ttk.Frame(self.recordings_tab)
        edit_controls.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(edit_controls, text="Edit", command=self.edit_recording).pack(side="left")
        ttk.Button(edit_controls, text="Delete", command=self.delete_recording).pack(side="left", padx=(5, 0))
        ttk.Button(edit_controls, text="↑", width=3, command=lambda: self.move_recording(-1)).pack(side="right")
        ttk.Button(edit_controls, text="↓", width=3, command=lambda: self.move_recording(1)).pack(
            side="right", padx=(0, 5)
        )
        self.recordings_tree.bind("<Double-1>", lambda _e: self.edit_recording())

    def refresh_recordings(self) -> None:
        if not hasattr(self, "recordings_tree"):
            return
        self.recordings_tree.delete(*self.recordings_tree.get_children())
        hymn = self.selected_hymn()
        if not hymn:
            return
        for index, recording in enumerate(hymn.get("recordings", [])):
            recording_type = str(recording.get("type", "soundcloud") or "soundcloud").lower()
            if recording_type == "audio":
                source_type = str(recording.get("source_type", "upload") or "upload").lower()
                display_type = "YouTube audio" if source_type == "youtube" else "Audio file"
                source = recording.get("source_url", "") or recording.get("audio_file", "")
            else:
                display_type = "SoundCloud"
                source = recording.get("url", "")
            self.recordings_tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    display_type,
                    recording.get("label", "Recording"),
                    source,
                    recording.get("start_at", "0:00") or "0:00",
                    recording.get("end_at", "") or "—",
                    "Yes" if recording.get("published", True) else "No",
                ),
            )

    def recording_index(self) -> int | None:
        selection = self.recordings_tree.selection()
        return int(selection[0]) if selection else None

    def _validate_recording_dialog_result(self, result: dict[str, Any]) -> bool:
        try:
            result["start_at"] = validate_time_text(
                str(result.get("start_at", "0:00")),
                field_name="start time",
            )
            result["end_at"] = validate_optional_time_text(
                str(result.get("end_at", "")),
                field_name="end time",
            )
            start_ms = time_text_to_ms(result["start_at"])
            end_ms = time_text_to_ms(result["end_at"]) if result["end_at"] else 0
            if end_ms and end_ms <= start_ms:
                raise ValueError("End at must be later than Start at.")
        except ValueError as exc:
            messagebox.showerror("Invalid recording range", str(exc), parent=self)
            return False
        return True

    def add_recording(self) -> None:
        hymn = self.selected_hymn()
        if not hymn:
            messagebox.showinfo("Select a hymn", "Select a hymn first.", parent=self)
            return
        dialog = RecordDialog(self, "Add SoundCloud recording", [
            ("Label", "label", "text", "Recording"),
            ("Full SoundCloud track URL", "url", "text", "https://soundcloud.com/"),
            ("Start at (e.g. 0:00 or 1:23)", "start_at", "text", "0:00"),
            ("End at (blank = end of track)", "end_at", "text", ""),
            ("Published", "published", "bool", True),
        ])
        if not dialog.result or not self._validate_recording_dialog_result(dialog.result):
            return
        dialog.result["type"] = "soundcloud"
        items = hymn.setdefault("recordings", [])
        dialog.result["sort"] = next_sort(items)
        items.append(dialog.result)
        self.mark_dirty()
        self.refresh_recordings()

    def import_youtube_recording(self) -> None:
        hymn = self.selected_hymn()
        if not hymn:
            messagebox.showinfo("Select a hymn", "Select a hymn first.", parent=self)
            return
        if not self.client:
            messagebox.showerror("Not connected", "Sign in to the website first.", parent=self)
            return

        dialog = RecordDialog(self, "Import YouTube audio", [
            ("YouTube URL", "url", "text", "https://www.youtube.com/watch?v="),
            ("Label (blank = YouTube title)", "label", "text", ""),
            ("Start at (e.g. 0:00 or 1:23)", "start_at", "text", "0:00"),
            ("End at (blank = end of track)", "end_at", "text", ""),
            ("Published", "published", "bool", True),
            (
                "I own this recording or have permission to store/use it",
                "confirm_rights",
                "bool",
                False,
            ),
        ])
        if not dialog.result:
            return
        if not bool(dialog.result.get("confirm_rights")):
            messagebox.showerror(
                "Permission confirmation required",
                "Confirm that you own the recording or have permission to store and use it.",
                parent=self,
            )
            return
        if not self._validate_recording_dialog_result(dialog.result):
            return
        url = str(dialog.result.get("url", "")).strip()
        if not url:
            messagebox.showerror("Missing YouTube URL", "Enter a YouTube URL.", parent=self)
            return

        wanted_label = str(dialog.result.get("label", "")).strip()
        start_at = dialog.result["start_at"]
        end_at = dialog.result.get("end_at", "")
        published = bool(dialog.result.get("published", True))

        def work() -> dict[str, Any]:
            assert self.client is not None
            return self.client.import_youtube_audio(url)

        def done(result: dict[str, Any]) -> None:
            recording = dict(result.get("recording") or {})
            if not recording.get("audio_file"):
                raise RuntimeError("The website did not return the imported audio file.")
            recording["type"] = "audio"
            recording["label"] = wanted_label or str(recording.get("label") or "YouTube recording")
            recording["start_at"] = start_at
            recording["end_at"] = end_at
            recording["published"] = published
            items = hymn.setdefault("recordings", [])
            recording["sort"] = next_sort(items)
            items.append(recording)
            self.mark_dirty()
            self.refresh_recordings()
            messagebox.showinfo(
                "YouTube audio imported",
                "The audio is now stored on your Raspberry Pi and added to this draft.\n\n"
                "Publish the content when you are ready to make it live.",
                parent=self,
            )

        self.run_async("Importing YouTube audio and building waveform…", work, done)

    def upload_audio_recording(self) -> None:
        hymn = self.selected_hymn()
        if not hymn:
            messagebox.showinfo("Select a hymn", "Select a hymn first.", parent=self)
            return
        if not self.client:
            messagebox.showerror("Not connected", "Sign in to the website first.", parent=self)
            return

        path = filedialog.askopenfilename(
            parent=self,
            title="Choose hymn audio",
            filetypes=[
                ("Audio files", "*.mp3 *.m4a *.aac *.wav *.ogg *.opus *.webm *.flac"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        dialog = RecordDialog(self, "Upload audio recording", [
            ("Label", "label", "text", Path(path).stem),
            ("Start at (e.g. 0:00 or 1:23)", "start_at", "text", "0:00"),
            ("End at (blank = end of track)", "end_at", "text", ""),
            ("Published", "published", "bool", True),
        ])
        if not dialog.result or not self._validate_recording_dialog_result(dialog.result):
            return
        wanted_label = str(dialog.result.get("label", "")).strip() or Path(path).stem
        start_at = dialog.result["start_at"]
        end_at = dialog.result.get("end_at", "")
        published = bool(dialog.result.get("published", True))

        def work() -> dict[str, Any]:
            assert self.client is not None
            return self.client.upload_hymn_audio(path)

        def done(result: dict[str, Any]) -> None:
            recording = dict(result.get("recording") or {})
            if not recording.get("audio_file"):
                raise RuntimeError("The website did not return the uploaded audio file.")
            recording["type"] = "audio"
            recording["label"] = wanted_label
            recording["start_at"] = start_at
            recording["end_at"] = end_at
            recording["published"] = published
            items = hymn.setdefault("recordings", [])
            recording["sort"] = next_sort(items)
            items.append(recording)
            self.mark_dirty()
            self.refresh_recordings()

        self.run_async("Uploading audio and building waveform…", work, done)

    def edit_recording(self) -> None:
        hymn = self.selected_hymn()
        index = self.recording_index()
        if not hymn or index is None:
            return
        item = hymn["recordings"][index]
        recording_type = str(item.get("type", "soundcloud") or "soundcloud").lower()

        if recording_type == "audio":
            dialog = RecordDialog(self, "Edit self-hosted recording", [
                ("Label", "label", "text", "Recording"),
                ("Start at (e.g. 0:00 or 1:23)", "start_at", "text", "0:00"),
                ("End at (blank = end of track)", "end_at", "text", ""),
                ("Published", "published", "bool", True),
            ], item)
            if not dialog.result or not self._validate_recording_dialog_result(dialog.result):
                return
            updated = dict(item)
            updated.update(dialog.result)
            updated["type"] = "audio"
            updated["sort"] = item.get("sort", (index + 1) * 10)
            hymn["recordings"][index] = updated
        else:
            dialog = RecordDialog(self, "Edit SoundCloud recording", [
                ("Label", "label", "text", "Recording"),
                ("Full SoundCloud track URL", "url", "text", ""),
                ("Start at (e.g. 0:00 or 1:23)", "start_at", "text", "0:00"),
                ("End at (blank = end of track)", "end_at", "text", ""),
                ("Published", "published", "bool", True),
            ], item)
            if not dialog.result or not self._validate_recording_dialog_result(dialog.result):
                return
            dialog.result["type"] = "soundcloud"
            dialog.result["sort"] = item.get("sort", (index + 1) * 10)
            hymn["recordings"][index] = dialog.result

        self.mark_dirty()
        self.refresh_recordings()

    def delete_recording(self) -> None:
        hymn = self.selected_hymn()
        index = self.recording_index()
        if not hymn or index is None:
            return
        item = hymn["recordings"][index]
        is_managed_audio = str(item.get("type", "soundcloud") or "soundcloud").lower() == "audio"
        prompt = "Delete this recording from the hymn?"
        if is_managed_audio:
            prompt += (
                "\n\nWhen this deletion is published, the MP3 will also be deleted from your "
                "Raspberry Pi if no other hymn still uses the same file."
            )
        if not messagebox.askyesno("Delete recording", prompt, parent=self):
            return

        audio_file = str(item.get("audio_file", "")).strip() if is_managed_audio else ""
        del hymn["recordings"][index]
        resequence(hymn["recordings"])
        self.mark_dirty()
        self.refresh_recordings()

        # A file imported during this draft is safe to delete immediately if the
        # currently published JSON does not reference it. Published files remain
        # until the user publishes the deletion, preventing a broken live player.
        if audio_file and self.client and audio_file not in managed_audio_files(self.content):
            def work() -> dict[str, Any]:
                assert self.client is not None
                return self.client.delete_unpublished_audio(audio_file)

            def done(result: dict[str, Any]) -> None:
                if result.get("deleted"):
                    return
                # It is still referenced by the live site (or another hymn), so
                # save_editable_site will handle it safely when content is published.

            self.run_async("Checking managed audio file…", work, done)

    def move_recording(self, direction: int) -> None:
        hymn = self.selected_hymn()
        index = self.recording_index()
        if not hymn or index is None:
            return
        items = hymn["recordings"]
        new = index + direction
        if new < 0 or new >= len(items):
            return
        items[index], items[new] = items[new], items[index]
        resequence(items)
        self.mark_dirty()
        self.refresh_recordings()
        self.recordings_tree.selection_set(str(new))

    def build_lyrics_tab(self) -> None:
        ttk.Label(self.lyrics_tab, text="Timestamped lyrics", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.lyrics_tab, text="Each row may contain text for every configured language.").pack(anchor="w", pady=(3, 8))
        table_frame = ttk.Frame(self.lyrics_tab)
        table_frame.pack(fill="both", expand=True)
        self.lyrics_tree = ttk.Treeview(table_frame, show="headings")
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.lyrics_tree.xview)
        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.lyrics_tree.yview)
        self.lyrics_tree.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)
        self.lyrics_tree.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        controls = ttk.Frame(self.lyrics_tab)
        controls.pack(fill="x", pady=(8, 0))
        ttk.Button(controls, text="Add lyric row", command=self.add_lyric).pack(side="left")
        ttk.Button(
            controls,
            text="Bulk import lyrics",
            style="Primary.TButton",
            command=self.bulk_import_lyrics,
        ).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="Edit", command=self.edit_lyric).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="Delete", command=self.delete_lyric).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="↑", width=3, command=lambda: self.move_lyric(-1)).pack(side="right")
        ttk.Button(controls, text="↓", width=3, command=lambda: self.move_lyric(1)).pack(side="right", padx=(0, 5))
        self.lyrics_tree.bind("<Double-1>", lambda _e: self.edit_lyric())

    def refresh_lyrics(self) -> None:
        if not hasattr(self, "lyrics_tree"):
            return
        languages = self.content.get("languages", [])
        columns = ["t"] + [lang.get("code", "") for lang in languages] + ["published"]
        self.lyrics_tree.configure(columns=columns)
        self.lyrics_tree.heading("t", text="Time")
        self.lyrics_tree.column("t", width=90, stretch=False)
        for lang in languages:
            code = lang.get("code", "")
            self.lyrics_tree.heading(code, text=lang.get("name", code))
            self.lyrics_tree.column(code, width=260, stretch=True)
        self.lyrics_tree.heading("published", text="Published")
        self.lyrics_tree.column("published", width=90, stretch=False)
        self.lyrics_tree.delete(*self.lyrics_tree.get_children())
        hymn = self.selected_hymn()
        if not hymn:
            return
        for index, segment in enumerate(hymn.get("segments", [])):
            values = [segment.get("t", "0:00")]
            texts = segment.get("texts") or {}
            for lang in languages:
                text = str(texts.get(lang.get("code", ""), "")).replace("\n", " / ")
                values.append(text[:140])
            values.append("Yes" if segment.get("published", True) else "No")
            self.lyrics_tree.insert("", "end", iid=str(index), values=values)

    def lyric_index(self) -> int | None:
        selection = self.lyrics_tree.selection()
        return int(selection[0]) if selection else None

    def add_lyric(self) -> None:
        hymn = self.selected_hymn()
        if not hymn:
            messagebox.showinfo("Select a hymn", "Select a hymn first.", parent=self)
            return
        dialog = LyricDialog(self, self.content.get("languages", []))
        if not dialog.result:
            return
        items = hymn.setdefault("segments", [])
        dialog.result["sort"] = next_sort(items)
        items.append(dialog.result)
        self.mark_dirty()
        self.refresh_lyrics()

    def bulk_import_lyrics(self) -> None:
        hymn = self.selected_hymn()
        if not hymn:
            messagebox.showinfo("Select a hymn", "Select a hymn first.", parent=self)
            return
        if not self.client:
            messagebox.showerror(
                "Not connected",
                "Sign in to the website before using the bulk lyric importer.",
                parent=self,
            )
            return

        dialog = BulkLyricImportDialog(
            self,
            self.client,
            str(hymn.get("title", "Selected hymn")),
            self.content.get("languages", []),
        )
        if not dialog.result:
            return

        new_segments = deepcopy(dialog.result.get("segments") or [])
        if not new_segments:
            return

        mode = str(dialog.result.get("mode", "replace"))
        existing = hymn.setdefault("segments", [])

        if mode == "replace" and existing:
            if not messagebox.askyesno(
                "Replace existing lyrics",
                (
                    f"This hymn currently contains {len(existing)} lyric row(s).\n\n"
                    f"Replace them with the {len(new_segments)} imported row(s)?"
                ),
                parent=self,
            ):
                return

        if mode == "append":
            existing.extend(new_segments)
            resequence(existing)
        else:
            hymn["segments"] = new_segments
            resequence(hymn["segments"])

        self.mark_dirty()
        self.refresh_lyrics()
        self.status_label.configure(
            text=f"Imported {len(new_segments)} lyric rows into {hymn.get('title', 'the hymn')}."
        )

    def edit_lyric(self) -> None:
        hymn = self.selected_hymn()
        index = self.lyric_index()
        if not hymn or index is None:
            return
        item = hymn["segments"][index]
        dialog = LyricDialog(self, self.content.get("languages", []), item)
        if not dialog.result:
            return
        dialog.result["sort"] = item.get("sort", (index + 1) * 10)
        hymn["segments"][index] = dialog.result
        self.mark_dirty()
        self.refresh_lyrics()

    def delete_lyric(self) -> None:
        hymn = self.selected_hymn()
        index = self.lyric_index()
        if not hymn or index is None:
            return
        if messagebox.askyesno("Delete lyric row", "Delete this timestamped lyric row?", parent=self):
            del hymn["segments"][index]
            resequence(hymn["segments"])
            self.mark_dirty()
            self.refresh_lyrics()

    def move_lyric(self, direction: int) -> None:
        hymn = self.selected_hymn()
        index = self.lyric_index()
        if not hymn or index is None:
            return
        items = hymn["segments"]
        new = index + direction
        if new < 0 or new >= len(items):
            return
        items[index], items[new] = items[new], items[index]
        resequence(items)
        self.mark_dirty()
        self.refresh_lyrics()
        self.lyrics_tree.selection_set(str(new))

    def build_languages_tab(self) -> None:
        ttk.Label(self.languages_tab, text="Languages", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.languages_tab, text="Changing a language code changes which lyric field the website reads.").pack(anchor="w", pady=(3, 8))
        self.languages_tree = ttk.Treeview(self.languages_tab, columns=("code", "name", "default", "rtl"), show="headings", height=12)
        for col, title, width in [("code", "Code", 120), ("name", "Name", 220), ("default", "Default on", 100), ("rtl", "RTL", 80)]:
            self.languages_tree.heading(col, text=title)
            self.languages_tree.column(col, width=width, stretch=col == "name")
        self.languages_tree.pack(fill="both", expand=True, pady=(0, 8))
        controls = ttk.Frame(self.languages_tab)
        controls.pack(fill="x")
        ttk.Button(controls, text="Add", command=self.add_language).pack(side="left")
        ttk.Button(controls, text="Edit", command=self.edit_language).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="Delete", command=self.delete_language).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="↑", width=3, command=lambda: self.move_language(-1)).pack(side="right")
        ttk.Button(controls, text="↓", width=3, command=lambda: self.move_language(1)).pack(side="right", padx=(0, 5))
        self.languages_tree.bind("<Double-1>", lambda _e: self.edit_language())

    def refresh_languages(self) -> None:
        if not hasattr(self, "languages_tree"):
            return
        self.languages_tree.delete(*self.languages_tree.get_children())
        for index, lang in enumerate(self.content.get("languages", [])):
            self.languages_tree.insert("", "end", iid=str(index), values=(lang.get("code", ""), lang.get("name", ""), "Yes" if lang.get("default_on", True) else "No", "Yes" if lang.get("is_rtl", False) else "No"))
        self.refresh_lyrics()

    def language_index(self) -> int | None:
        selection = self.languages_tree.selection()
        return int(selection[0]) if selection else None

    def add_language(self) -> None:
        dialog = RecordDialog(self, "Add language", [
            ("Language code", "code", "text", ""),
            ("Display name", "name", "text", ""),
            ("Default visible", "default_on", "bool", True),
            ("Right-to-left", "is_rtl", "bool", False),
        ])
        if not dialog.result:
            return
        dialog.result["code"] = dialog.result["code"].strip().lower()
        languages = self.content.setdefault("languages", [])
        dialog.result["sort"] = next_sort(languages)
        languages.append(dialog.result)
        self.mark_dirty()
        self.refresh_languages()

    def edit_language(self) -> None:
        index = self.language_index()
        if index is None:
            return
        languages = self.content.get("languages", [])
        item = languages[index]
        old_code = item.get("code", "")
        dialog = RecordDialog(self, "Edit language", [
            ("Language code", "code", "text", old_code),
            ("Display name", "name", "text", item.get("name", "")),
            ("Default visible", "default_on", "bool", item.get("default_on", True)),
            ("Right-to-left", "is_rtl", "bool", item.get("is_rtl", False)),
        ], item)
        if not dialog.result:
            return
        new_code = dialog.result["code"].strip().lower()
        if new_code != old_code:
            if messagebox.askyesno("Rename language code", f"Rename lyric fields from '{old_code}' to '{new_code}' throughout every hymn?", parent=self):
                for level in self.content.get("levels", []):
                    for year in level.get("years", []):
                        for hymn in year.get("hymns", []):
                            for segment in hymn.get("segments", []):
                                texts = segment.setdefault("texts", {})
                                if old_code in texts and new_code not in texts:
                                    texts[new_code] = texts.pop(old_code)
            else:
                return
        dialog.result["code"] = new_code
        dialog.result["sort"] = item.get("sort", (index + 1) * 10)
        languages[index] = dialog.result
        self.mark_dirty()
        self.refresh_languages()

    def delete_language(self) -> None:
        index = self.language_index()
        if index is None:
            return
        languages = self.content.get("languages", [])
        if len(languages) <= 1:
            messagebox.showerror("Cannot delete", "The website must have at least one language.", parent=self)
            return
        code = languages[index].get("code", "")
        if not messagebox.askyesno("Delete language", f"Delete '{languages[index].get('name', code)}' and remove its lyric text from every hymn?", parent=self):
            return
        del languages[index]
        resequence(languages)
        for level in self.content.get("levels", []):
            for year in level.get("years", []):
                for hymn in year.get("hymns", []):
                    for segment in hymn.get("segments", []):
                        segment.setdefault("texts", {}).pop(code, None)
        self.mark_dirty()
        self.refresh_languages()

    def move_language(self, direction: int) -> None:
        index = self.language_index()
        if index is None:
            return
        items = self.content["languages"]
        new = index + direction
        if new < 0 or new >= len(items):
            return
        items[index], items[new] = items[new], items[index]
        resequence(items)
        self.mark_dirty()
        self.refresh_languages()
        self.languages_tree.selection_set(str(new))

    def build_site_tab(self) -> None:
        ttk.Label(self.site_tab, text="Public site text", style="Heading.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self.site_title_var = tk.StringVar()
        self.site_subtitle_var = tk.StringVar()
        self.footer_var = tk.StringVar()
        for row, (label, var) in enumerate([
            ("Site title", self.site_title_var),
            ("Site subtitle", self.site_subtitle_var),
            ("Footer text", self.footer_var),
        ], start=1):
            ttk.Label(self.site_tab, text=label).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Entry(self.site_tab, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(12, 0), pady=7)
        ttk.Button(self.site_tab, text="Save site settings", command=self.save_site_settings).grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        self.site_tab.columnconfigure(1, weight=1)

    def populate_site_settings(self) -> None:
        self.site_title_var.set(str(self.content.get("site_title", "")))
        self.site_subtitle_var.set(str(self.content.get("site_subtitle", "")))
        self.footer_var.set(str(self.content.get("footer_text", "")))

    def save_site_settings(self) -> None:
        self.content["site_title"] = self.site_title_var.get().strip()
        self.content["site_subtitle"] = self.site_subtitle_var.get().strip()
        self.content["footer_text"] = self.footer_var.get().strip()
        self.mark_dirty()
        self.status_label.configure(text="Site settings saved to the local draft.")

    def build_publish_tab(self) -> None:
        ttk.Label(self.publish_tab, text="Publish to the live website", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            self.publish_tab,
            text="Publishing updates the persistent website content immediately. It does not restart Docker or Portainer.",
            wraplength=760,
        ).pack(anchor="w", pady=(4, 16))
        self.github_backup_var = tk.BooleanVar(value=True)
        self.github_check = ttk.Checkbutton(self.publish_tab, text="Back up the published JSON to GitHub (if configured on the server)", variable=self.github_backup_var)
        self.github_check.pack(anchor="w", pady=(0, 7))
        self.redeploy_after_publish_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.publish_tab,
            text="Also trigger Portainer pull/redeploy after publishing (normally leave this off for content-only edits)",
            variable=self.redeploy_after_publish_var,
        ).pack(anchor="w", pady=(0, 14))
        ttk.Button(self.publish_tab, text="Validate draft", command=self.validate_content).pack(anchor="w", fill="x", pady=4)
        ttk.Button(self.publish_tab, text="PUBLISH CONTENT NOW", command=self.publish_content).pack(anchor="w", fill="x", pady=4)
        ttk.Separator(self.publish_tab).pack(fill="x", pady=18)
        ttk.Label(self.publish_tab, text="Website code deployment", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(
            self.publish_tab,
            text="Use this only after code/CSS/template changes have already been pushed to GitHub. Portainer will pull/redeploy through its server-side webhook.",
            wraplength=760,
        ).pack(anchor="w", pady=(4, 10))
        ttk.Button(self.publish_tab, text="Trigger Portainer redeploy", command=self.redeploy).pack(anchor="w", fill="x")
        self.publish_status = tk.Text(self.publish_tab, height=13, wrap="word", state="disabled")
        self.publish_status.pack(fill="both", expand=True, pady=(18, 0))
        self.update_publish_config_text()

    def update_publish_config_text(self) -> None:
        if not hasattr(self, "publish_status"):
            return
        lines = ["Connection ready."]
        if self.remote_status:
            lines.append(f"Live content updated: {self.remote_status.get('updated_at', 'unknown')}")
            lines.append(f"GitHub backup configured: {'yes' if self.remote_status.get('github_configured') else 'no'}")
            lines.append(f"Portainer redeploy configured: {'yes' if self.remote_status.get('portainer_configured') else 'no'}")
        self.set_publish_text("\n".join(lines))

    def set_publish_text(self, text: str) -> None:
        if not hasattr(self, "publish_status"):
            return
        self.publish_status.configure(state="normal")
        self.publish_status.delete("1.0", "end")
        self.publish_status.insert("1.0", text)
        self.publish_status.configure(state="disabled")

    def validate_content(self) -> None:
        if not self.client:
            return
        draft = deepcopy(self.content)
        self.run_async(
            "Validating content…",
            lambda: self.client.validate(draft),
            lambda result: self.set_publish_text("Validation passed.\n" + ("\n".join(result.get("warnings", [])) or "No warnings.")),
        )

    def publish_content(self) -> None:
        if not self.client:
            return
        if not messagebox.askyesno(
            "Publish content",
            "Publish this curriculum to the live Hymns School website now?\n\nChanges will become visible without a Portainer redeploy.",
            parent=self,
        ):
            return
        draft = deepcopy(self.content)
        github_backup = bool(self.github_backup_var.get())
        redeploy_after_publish = bool(self.redeploy_after_publish_var.get())

        def work() -> dict[str, Any]:
            result = self.client.publish(draft, github_backup, self.remote_revision)
            if redeploy_after_publish:
                try:
                    result["redeploy"] = self.client.redeploy()
                except ApiError as exc:
                    result["redeploy_error"] = str(exc)
            return result

        def done(result: dict[str, Any]) -> None:
            self.dirty = False
            self.update_dirty_label()
            self.remote_status = result.get("status") or self.remote_status
            self.remote_revision = str(self.remote_status.get("revision", self.remote_revision))
            lines = [result.get("message", "Published.")]
            warnings = result.get("warnings") or []
            if warnings:
                lines.append("\nWarnings:")
                lines.extend(f"• {item}" for item in warnings)
            if result.get("backup_created"):
                lines.append(f"\nServer backup: {result['backup_created']}")
            if result.get("github"):
                sha = result["github"].get("commit_sha", "")
                lines.append(f"GitHub backup completed{f' ({sha[:8]})' if sha else ''}.")
            if result.get("github_error"):
                lines.append(f"GitHub backup warning: {result['github_error']}")
            if result.get("redeploy"):
                lines.append("Portainer redeploy was triggered after publish.")
            if result.get("redeploy_error"):
                lines.append(f"Portainer redeploy warning: {result['redeploy_error']}")
            self.set_publish_text("\n".join(lines))
            self.status_label.configure(text="Published successfully.")
            messagebox.showinfo("Published", "The live website content was updated successfully.", parent=self)

        self.run_async("Publishing website content…", work, done)

    def redeploy(self) -> None:
        if not self.client:
            return
        if not messagebox.askyesno(
            "Redeploy website",
            "Trigger the Portainer stack webhook now?\n\nOnly do this after website code changes have been pushed to GitHub.",
            parent=self,
        ):
            return
        self.run_async(
            "Triggering Portainer redeploy…",
            self.client.redeploy,
            lambda result: self.set_publish_text(result.get("message", "Portainer redeploy triggered.")),
        )

    def refresh_remote(self) -> None:
        if not self.client:
            return
        if self.dirty and not messagebox.askyesno("Discard local edits", "Reloading will discard unpublished local changes. Continue?", parent=self):
            return

        def done(result: dict[str, Any]) -> None:
            self.content = result.get("content") or default_content()
            self.remote_status = result.get("status") or {}
            self.remote_revision = str(self.remote_status.get("revision", ""))
            self.dirty = False
            self.populate_site_settings()
            self.refresh_languages()
            self.rebuild_tree()
            self.update_dirty_label()
            self.update_publish_config_text()
            self.status_label.configure(text="Reloaded from live website.")

        self.run_async("Loading current website content…", self.client.current, done)

    def save_draft(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Save local draft",
            defaultextension=".json",
            filetypes=[("St. Mina draft", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        wrapper = {
            "manager_version": APP_VERSION,
            "base_revision": self.remote_revision,
            "content": self.content,
        }
        Path(filename).write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.status_label.configure(text=f"Local draft saved: {filename}")

    def export_json(self) -> None:
        filename = filedialog.asksaveasfilename(
            parent=self,
            title="Export raw site JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        Path(filename).write_text(json.dumps(self.content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.status_label.configure(text=f"Raw JSON exported: {filename}")

    def open_draft(self) -> None:
        filename = filedialog.askopenfilename(parent=self, title="Open local draft", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not filename:
            return
        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("The JSON file does not contain a site object.")
            if isinstance(data.get("content"), dict):
                self.content = data["content"]
                self.remote_revision = str(data.get("base_revision", ""))
            else:
                # Raw exported JSON is also accepted. Keep the revision from the live
                # website loaded at sign-in so a concurrent remote change is still
                # detected before this imported content can overwrite it.
                self.content = data
        except Exception as exc:
            messagebox.showerror("Could not open draft", str(exc), parent=self)
            return
        self.mark_dirty()
        self.populate_site_settings()
        self.refresh_languages()
        self.rebuild_tree()
        self.status_label.configure(text=f"Loaded local draft: {filename}")

    def on_close(self) -> None:
        if self.dirty and not messagebox.askyesno("Unpublished changes", "You have unpublished local changes. Exit anyway?", parent=self):
            return
        self.destroy()


def main() -> None:
    app = ContentManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
