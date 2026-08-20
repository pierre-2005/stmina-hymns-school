from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import tkinter as tk
import unicodedata
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk, font as tkfont
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

APP_NAME = "St. Mina Hymns School Content Manager"
APP_VERSION = "3.2"
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
    "ⲇ\u0305": "ä",
    "ⲩ\u0305": "ö",
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


def unicode_coptic_to_avva(text: str) -> str:
    """
    Convert Unicode Coptic to the legacy Avva Shenouda encoding.

    In addition to the original converter mapping, this handles Unicode
    combining grave accents in the form expected by Avva Shenouda.
    Example: ⲉ + U+0300 -> `e
    """
    text = unicodedata.normalize("NFD", str(text or ""))
    output: list[str] = []
    i = 0

    while i < len(text):
        if i + 1 < len(text):
            pair = text[i:i + 2]
            if pair in SPECIAL_SEQUENCES:
                output.append(SPECIAL_SEQUENCES[pair])
                i += 2
                continue

        ch = text[i]

        if ch in UNICODE_TO_AVVA:
            mapped = UNICODE_TO_AVVA[ch]

            j = i + 1
            combining_marks: list[str] = []
            while j < len(text) and unicodedata.combining(text[j]):
                combining_marks.append(text[j])
                j += 1

            prefix = ""
            suffix = ""
            for mark in combining_marks:
                if mark == "\u0300":
                    prefix += "`"
                else:
                    suffix += mark

            output.append(prefix + mapped + suffix)
            i = j
            continue

        output.append(ch)
        i += 1

    return "".join(output)


def avva_to_unicode_coptic(text: str) -> str:
    """Best-effort reverse conversion for editing existing legacy lyrics."""
    text = str(text or "")
    output: list[str] = []
    i = 0

    while i < len(text):
        ch = text[i]

        if ch in AVVA_SPECIAL_TO_UNICODE:
            output.append(AVVA_SPECIAL_TO_UNICODE[ch])
            i += 1
            continue

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
    """Standalone Unicode Coptic -> Avva converter built into the manager."""

    def __init__(
        self,
        parent: tk.Misc,
        initial_unicode: str = "",
        initial_avva: str = "",
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
            text="Unicode / Noto Sans Coptic  →  Legacy Avva Shenouda",
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
            text="Unicode Coptic",
            bg=PAPER,
            fg=BURGUNDY_950,
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w", pady=(0, 7))

        tk.Label(
            card,
            text="Avva Shenouda output",
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
            text="Convert →",
            style="Primary.TButton",
            command=self.convert,
        ).pack(pady=(65, 8), fill="x")

        ttk.Button(
            controls,
            text="Clear",
            style="Quiet.TButton",
            command=self.clear,
        ).pack(pady=8, fill="x")

        output_font = (self.avva_family or "Courier New", 18)
        self.output_text = tk.Text(
            card,
            wrap="word",
            undo=True,
            font=output_font,
            padx=12,
            pady=12,
            relief="flat",
            highlightbackground=LINE,
            highlightthickness=1,
        )
        self.output_text.grid(row=1, column=2, sticky="nsew")
        self.output_text.insert("1.0", initial_avva)

        bottom = tk.Frame(card, bg=PAPER)
        bottom.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(12, 0))

        if self.avva_family:
            status_text = f"Avva preview font detected: {self.avva_family}"
        else:
            status_text = (
                "Avva font is not installed on this computer. Conversion still works; "
                "the output preview may look like Latin characters."
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
                text="Use converted text",
                style="Primary.TButton",
                command=self.use_output,
            ).pack(side="right")

        ttk.Button(
            bottom,
            text="Close",
            style="Quiet.TButton",
            command=self.destroy,
        ).pack(side="right", padx=(0, 8))

        self.bind("<Control-Return>", lambda _e: self.convert())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.input_text.focus_set()

    def convert(self) -> None:
        source = self.input_text.get("1.0", "end-1c")
        if not source.strip():
            messagebox.showinfo(
                "Nothing to convert",
                "Paste Unicode Coptic into the left box first.",
                parent=self,
            )
            return

        converted = unicode_coptic_to_avva(source)
        self.output_text.delete("1.0", "end")
        self.output_text.insert("1.0", converted)
        self.status.configure(text=f"Converted {len(source)} characters.")

    def clear(self) -> None:
        self.input_text.delete("1.0", "end")
        self.output_text.delete("1.0", "end")
        self.input_text.focus_set()

    def use_output(self) -> None:
        text = self.output_text.get("1.0", "end-1c")
        if not text.strip():
            self.convert()
            text = self.output_text.get("1.0", "end-1c")
        if text.strip() and self.on_use:
            self.on_use(text)
            self.destroy()


class LyricDialog(tk.Toplevel):
    """
    Timestamped lyric editor.

    For the language code 'cop', Unicode Coptic can be pasted directly.
    The manager converts it to Avva Shenouda legacy text before saving.
    Existing Avva text can also be edited directly.
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
                    text="Paste Unicode Coptic here",
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
                if existing:
                    unicode_box.insert("1.0", avva_to_unicode_coptic(existing))
                self.coptic_unicode_widget = unicode_box

                convert_row = tk.Frame(section, bg=section["bg"])
                convert_row.pack(fill="x", pady=7)

                ttk.Button(
                    convert_row,
                    text="Convert Unicode → Avva",
                    style="Primary.TButton",
                    command=self._convert_coptic_inline,
                ).pack(side="left")

                tk.Label(
                    convert_row,
                    text="The Avva version below is what will be saved to the website.",
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
                    text="Avva Shenouda output / saved value",
                    bg=section["bg"],
                    fg=MUTED,
                    font=("Segoe UI", 9),
                ).pack(anchor="w", pady=(2, 4))

                avva_box = tk.Text(
                    section,
                    height=5,
                    wrap="word",
                    font=(avva_family or "Courier New", 16),
                    padx=10,
                    pady=9,
                    relief="flat",
                    highlightbackground=LINE,
                    highlightthickness=1,
                )
                avva_box.pack(fill="x")
                avva_box.insert("1.0", existing)
                self.coptic_avva_widget = avva_box
                self.text_widgets[code] = avva_box

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

    def _convert_coptic_inline(self) -> None:
        if not self.coptic_unicode_widget or not self.coptic_avva_widget:
            return
        source = self.coptic_unicode_widget.get("1.0", "end-1c")
        converted = unicode_coptic_to_avva(source)
        self.coptic_avva_widget.delete("1.0", "end")
        self.coptic_avva_widget.insert("1.0", converted)

    def _open_converter(self) -> None:
        unicode_text = (
            self.coptic_unicode_widget.get("1.0", "end-1c")
            if self.coptic_unicode_widget
            else ""
        )
        avva_text = (
            self.coptic_avva_widget.get("1.0", "end-1c")
            if self.coptic_avva_widget
            else ""
        )

        def use(text: str) -> None:
            if not self.coptic_avva_widget:
                return
            self.coptic_avva_widget.delete("1.0", "end")
            self.coptic_avva_widget.insert("1.0", text)
            if self.coptic_unicode_widget:
                self.coptic_unicode_widget.delete("1.0", "end")
                self.coptic_unicode_widget.insert("1.0", avva_to_unicode_coptic(text))

        CopticConverterDialog(
            self,
            initial_unicode=unicode_text,
            initial_avva=avva_text,
            on_use=use,
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

        # If the Unicode Coptic box contains Unicode Coptic, automatically
        # refresh the Avva value before saving. This removes the need to run
        # a separate converter program.
        if self.coptic_unicode_widget and self.coptic_avva_widget:
            source = self.coptic_unicode_widget.get("1.0", "end-1c").strip()
            if source and contains_unicode_coptic(source):
                converted = unicode_coptic_to_avva(source)
                self.coptic_avva_widget.delete("1.0", "end")
                self.coptic_avva_widget.insert("1.0", converted)

        texts = {
            code: widget.get("1.0", "end-1c").strip()
            for code, widget in self.text_widgets.items()
        }
        texts = {code: text for code, text in texts.items() if text}

        self.result = {
            "t": timestamp,
            "texts": texts,
            "published": bool(self.published_var.get()),
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
        if kind == "level":
            del self.content["levels"][li]
        elif kind == "year":
            del self.content["levels"][li]["years"][yi]
        else:
            del self.content["levels"][li]["years"][yi]["hymns"][hi]
        self.current_ref = None
        self.mark_dirty()
        self.rebuild_tree()

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
        ttk.Label(self.recordings_tab, text="SoundCloud recordings", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.recordings_tab, text="Select a hymn in the curriculum tree to manage its recordings.").pack(anchor="w", pady=(3, 8))
        self.recordings_tree = ttk.Treeview(self.recordings_tab, columns=("label", "url", "published"), show="headings", height=14)
        for col, title, width in [("label", "Label", 180), ("url", "SoundCloud URL", 480), ("published", "Published", 90)]:
            self.recordings_tree.heading(col, text=title)
            self.recordings_tree.column(col, width=width, stretch=True if col == "url" else False)
        self.recordings_tree.pack(fill="both", expand=True, pady=(0, 8))
        controls = ttk.Frame(self.recordings_tab)
        controls.pack(fill="x")
        ttk.Button(controls, text="Add", command=self.add_recording).pack(side="left")
        ttk.Button(controls, text="Edit", command=self.edit_recording).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="Delete", command=self.delete_recording).pack(side="left", padx=(5, 0))
        ttk.Button(controls, text="↑", width=3, command=lambda: self.move_recording(-1)).pack(side="right")
        ttk.Button(controls, text="↓", width=3, command=lambda: self.move_recording(1)).pack(side="right", padx=(0, 5))
        self.recordings_tree.bind("<Double-1>", lambda _e: self.edit_recording())

    def refresh_recordings(self) -> None:
        if not hasattr(self, "recordings_tree"):
            return
        self.recordings_tree.delete(*self.recordings_tree.get_children())
        hymn = self.selected_hymn()
        if not hymn:
            return
        for index, recording in enumerate(hymn.get("recordings", [])):
            self.recordings_tree.insert("", "end", iid=str(index), values=(recording.get("label", "Recording"), recording.get("url", ""), "Yes" if recording.get("published", True) else "No"))

    def recording_index(self) -> int | None:
        selection = self.recordings_tree.selection()
        return int(selection[0]) if selection else None

    def add_recording(self) -> None:
        hymn = self.selected_hymn()
        if not hymn:
            messagebox.showinfo("Select a hymn", "Select a hymn first.", parent=self)
            return
        dialog = RecordDialog(self, "Add SoundCloud recording", [
            ("Label", "label", "text", "Recording"),
            ("Full SoundCloud track URL", "url", "text", "https://soundcloud.com/"),
            ("Published", "published", "bool", True),
        ])
        if not dialog.result:
            return
        items = hymn.setdefault("recordings", [])
        dialog.result["sort"] = next_sort(items)
        items.append(dialog.result)
        self.mark_dirty()
        self.refresh_recordings()

    def edit_recording(self) -> None:
        hymn = self.selected_hymn()
        index = self.recording_index()
        if not hymn or index is None:
            return
        item = hymn["recordings"][index]
        dialog = RecordDialog(self, "Edit SoundCloud recording", [
            ("Label", "label", "text", "Recording"),
            ("Full SoundCloud track URL", "url", "text", ""),
            ("Published", "published", "bool", True),
        ], item)
        if not dialog.result:
            return
        dialog.result["sort"] = item.get("sort", (index + 1) * 10)
        hymn["recordings"][index] = dialog.result
        self.mark_dirty()
        self.refresh_recordings()

    def delete_recording(self) -> None:
        hymn = self.selected_hymn()
        index = self.recording_index()
        if not hymn or index is None:
            return
        if messagebox.askyesno("Delete recording", "Delete this recording from the hymn?", parent=self):
            del hymn["recordings"][index]
            resequence(hymn["recordings"])
            self.mark_dirty()
            self.refresh_recordings()

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
