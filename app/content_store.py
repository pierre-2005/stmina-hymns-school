from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .content_loader import ContentError, load_site, soundcloud_embed_url

SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
LANG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,19}$")


def legacy_content_path() -> Path:
    return Path(os.getenv("CONTENT_PATH", "/app/content/site.xlsx"))


def content_json_path() -> Path:
    return Path(os.getenv("CONTENT_JSON_PATH", "/app/data/site-content.json"))


def content_backup_dir() -> Path:
    return Path(os.getenv("CONTENT_BACKUP_DIR", "/app/data/content-backups"))


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_time_to_ms(value: Any) -> int:
    text = _clean(value)
    if not text:
        return 0
    try:
        parts = text.split(":")
        if len(parts) == 1:
            return max(0, int(round(float(parts[0]) * 1000)))
        seconds = float(parts[-1])
        leading = [int(part) for part in parts[:-1]]
        if len(leading) == 1:
            total = leading[0] * 60 + seconds
        elif len(leading) == 2:
            total = leading[0] * 3600 + leading[1] * 60 + seconds
        else:
            raise ValueError
        return max(0, int(round(total * 1000)))
    except (TypeError, ValueError) as exc:
        raise ContentError(f"Invalid timestamp '{text}'. Use 0:06.5, 2:15, or 1:02:03.") from exc


def _validate_soundcloud_url(url: str) -> None:
    text = _clean(url)
    if not text:
        raise ContentError("A SoundCloud recording URL cannot be blank.")

    # The existing player can consume normal track URLs and already-built widget URLs.
    # Short on.soundcloud.com links are intentionally rejected because they redirect and
    # are not reliable as the embedded widget target.
    from urllib.parse import urlparse

    parsed = urlparse(text)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host == "on.soundcloud.com":
        raise ContentError(
            "SoundCloud short links (on.soundcloud.com) are not supported. "
            "Open the link and paste the full soundcloud.com track URL instead."
        )
    if host not in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com", "w.soundcloud.com"}:
        raise ContentError(f"'{text}' is not a SoundCloud URL.")
    if not soundcloud_embed_url(text):
        raise ContentError(f"'{text}' could not be converted into a SoundCloud player URL.")


def canonicalise_site(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a storage-safe copy with derived runtime fields removed."""
    source = copy.deepcopy(raw or {})
    site: dict[str, Any] = {
        "schema_version": 1,
        "site_title": _clean(source.get("site_title")) or "St. Mina Hymns School",
        "site_subtitle": _clean(source.get("site_subtitle")) or "St. Mina Coptic Orthodox Church • Calgary, AB",
        "footer_text": _clean(source.get("footer_text")) or "St. Mina Coptic Orthodox Church (Calgary)",
        "languages": [],
        "levels": [],
    }

    for index, language in enumerate(source.get("languages", []) or []):
        if not isinstance(language, dict):
            continue
        site["languages"].append(
            {
                "code": _clean(language.get("code")).lower(),
                "name": _clean(language.get("name")),
                "is_rtl": _bool(language.get("is_rtl"), False),
                "default_on": _bool(language.get("default_on"), True),
                "sort": _int(language.get("sort"), index * 10),
            }
        )

    for level_index, level in enumerate(source.get("levels", []) or []):
        if not isinstance(level, dict):
            continue
        out_level = {
            "slug": _clean(level.get("slug")),
            "name": _clean(level.get("name")),
            "description": _clean(level.get("description")),
            "sort": _int(level.get("sort"), level_index * 10),
            "published": _bool(level.get("published"), True),
            "years": [],
        }
        for year_index, year in enumerate(level.get("years", []) or []):
            if not isinstance(year, dict):
                continue
            out_year = {
                "slug": _clean(year.get("slug")),
                "name": _clean(year.get("name")),
                "description": _clean(year.get("description")),
                "sort": _int(year.get("sort"), year_index * 10),
                "published": _bool(year.get("published"), True),
                "hymns": [],
            }
            for hymn_index, hymn in enumerate(year.get("hymns", []) or []):
                if not isinstance(hymn, dict):
                    continue
                out_hymn = {
                    "slug": _clean(hymn.get("slug")),
                    "title": _clean(hymn.get("title")),
                    "note": _clean(hymn.get("note")),
                    "sort": _int(hymn.get("sort"), hymn_index * 10),
                    "published": _bool(hymn.get("published"), True),
                    "recordings": [],
                    "segments": [],
                }

                for recording_index, recording in enumerate(hymn.get("recordings", []) or []):
                    if not isinstance(recording, dict):
                        continue
                    start_at = (
                        _clean(recording.get("start_at"))
                        or _clean(recording.get("start_time"))
                        or _clean(recording.get("start"))
                        or "0:00"
                    )
                    out_hymn["recordings"].append(
                        {
                            "label": _clean(recording.get("label")) or "Recording",
                            "url": _clean(recording.get("url")),
                            "start_at": start_at,
                            "sort": _int(recording.get("sort"), recording_index * 10),
                            "published": _bool(recording.get("published"), True),
                        }
                    )

                for segment_index, segment in enumerate(hymn.get("segments", []) or []):
                    if not isinstance(segment, dict):
                        continue
                    texts = {
                        _clean(code).lower(): str(text)
                        for code, text in (segment.get("texts") or {}).items()
                        if _clean(code) and text is not None and _clean(text)
                    }
                    out_hymn["segments"].append(
                        {
                            "t": _clean(segment.get("t")) or "0:00",
                            "texts": texts,
                            "sort": _int(segment.get("sort"), segment_index * 10),
                            "published": _bool(segment.get("published"), True),
                        }
                    )

                out_year["hymns"].append(out_hymn)
            out_level["years"].append(out_year)
        site["levels"].append(out_level)

    return site


def validate_site(raw: dict[str, Any]) -> list[str]:
    """Validate editable content. Raises ContentError for blocking errors; returns warnings."""
    site = canonicalise_site(raw)
    warnings: list[str] = []

    if not site["site_title"]:
        raise ContentError("Site title cannot be blank.")

    language_codes: set[str] = set()
    if not site["languages"]:
        raise ContentError("At least one language is required.")
    for language in site["languages"]:
        code = language["code"]
        if not LANG_RE.fullmatch(code):
            raise ContentError(
                f"Language code '{code}' is invalid. Use lowercase letters/numbers, dashes, or underscores."
            )
        if code in language_codes:
            raise ContentError(f"Language code '{code}' is duplicated.")
        language_codes.add(code)
        if not language["name"]:
            raise ContentError(f"Language '{code}' needs a display name.")

    level_slugs: set[str] = set()
    for level in site["levels"]:
        if not SLUG_RE.fullmatch(level["slug"]):
            raise ContentError(f"Level slug '{level['slug']}' is invalid.")
        if level["slug"] in level_slugs:
            raise ContentError(f"Level slug '{level['slug']}' is duplicated.")
        level_slugs.add(level["slug"])
        if not level["name"]:
            raise ContentError(f"Level '{level['slug']}' needs a name.")

        year_slugs: set[str] = set()
        for year in level["years"]:
            if not SLUG_RE.fullmatch(year["slug"]):
                raise ContentError(f"Year slug '{year['slug']}' in {level['name']} is invalid.")
            if year["slug"] in year_slugs:
                raise ContentError(f"Year slug '{year['slug']}' is duplicated inside {level['name']}.")
            year_slugs.add(year["slug"])
            if not year["name"]:
                raise ContentError(f"Year '{year['slug']}' needs a name.")

            hymn_slugs: set[str] = set()
            for hymn in year["hymns"]:
                if not SLUG_RE.fullmatch(hymn["slug"]):
                    raise ContentError(f"Hymn slug '{hymn['slug']}' in {year['name']} is invalid.")
                if hymn["slug"] in hymn_slugs:
                    raise ContentError(f"Hymn slug '{hymn['slug']}' is duplicated inside {year['name']}.")
                hymn_slugs.add(hymn["slug"])
                if not hymn["title"]:
                    raise ContentError(f"Hymn '{hymn['slug']}' needs a title.")

                for recording in hymn["recordings"]:
                    # Validate the optional per-recording start offset whether or
                    # not the recording is currently published, so drafts cannot
                    # carry an invalid value that surprises the user later.
                    _parse_time_to_ms(recording.get("start_at", "0:00"))
                    if recording["published"]:
                        _validate_soundcloud_url(recording["url"])

                previous_ms = -1
                for segment in sorted(hymn["segments"], key=lambda item: (item["sort"], item["t"])):
                    current_ms = _parse_time_to_ms(segment["t"])
                    unknown_languages = sorted(set(segment["texts"]) - language_codes)
                    if unknown_languages:
                        raise ContentError(
                            f"{hymn['title']} has lyric text for unknown language(s): "
                            + ", ".join(unknown_languages)
                        )
                    if segment["published"] and not segment["texts"]:
                        warnings.append(f"{hymn['title']} has a published lyric row at {segment['t']} with no text.")
                    if current_ms < previous_ms:
                        warnings.append(
                            f"{hymn['title']} lyric timestamps are not in chronological order; the website will sort them."
                        )
                    previous_ms = current_ms

    return warnings


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _backup_existing(path: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir = content_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = backup_dir / f"site-content-{timestamp}.json"
    counter = 1
    while destination.exists():
        destination = backup_dir / f"site-content-{timestamp}-{counter}.json"
        counter += 1
    shutil.copy2(path, destination)

    # Keep the most recent 50 automatic backups.
    backups = sorted(backup_dir.glob("site-content-*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in backups[50:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return destination


def ensure_content_json() -> Path:
    path = content_json_path()
    if path.exists():
        return path

    legacy = legacy_content_path()
    if not legacy.exists():
        starter = {
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
        canonical = canonicalise_site(starter)
    else:
        # One-time migration from the existing workbook. load_site gives us the same
        # content the live website currently renders, then canonicalise_site strips
        # runtime-only fields such as embed_url/start_ms.
        canonical = canonicalise_site(load_site(str(legacy)))

    validate_site(canonical)
    _atomic_write_json(path, canonical)
    return path


def load_editable_site() -> dict[str, Any]:
    path = ensure_content_json()
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContentError(f"Could not read editable content from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContentError("The site content file must contain one JSON object.")
    return canonicalise_site(value)


def save_editable_site(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str], Path | None]:
    canonical = canonicalise_site(raw)
    warnings = validate_site(canonical)
    path = ensure_content_json()
    backup = _backup_existing(path)
    _atomic_write_json(path, canonical)
    return canonical, warnings, backup


def content_revision() -> str:
    path = ensure_content_json()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def content_status() -> dict[str, Any]:
    path = ensure_content_json()
    stat = path.stat()
    return {
        "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "size_bytes": stat.st_size,
        "revision": content_revision(),
        "github_configured": github_is_configured(),
        "portainer_configured": portainer_is_configured(),
    }


def github_is_configured() -> bool:
    return bool(os.getenv("GITHUB_TOKEN", "").strip() and os.getenv("GITHUB_REPO", "").strip())


def backup_to_github(content: dict[str, Any], *, message: str = "Update hymn school content") -> dict[str, Any]:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repo = os.getenv("GITHUB_REPO", "").strip()
    branch = os.getenv("GITHUB_BRANCH", "main").strip() or "main"
    target_path = os.getenv("GITHUB_CONTENT_PATH", "content/site-content.json").strip() or "content/site-content.json"
    if not token or not repo or "/" not in repo:
        raise ContentError("GitHub backup is not configured on the server.")

    owner, name = repo.split("/", 1)
    encoded_path = "/".join(quote(part, safe="") for part in target_path.split("/"))
    base_url = f"https://api.github.com/repos/{quote(owner, safe='')}/{quote(name, safe='')}/contents/{encoded_path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2026-03-10",
        "User-Agent": "stmina-hymns-school-content-publisher",
    }

    sha: str | None = None
    try:
        request = Request(f"{base_url}?ref={quote(branch, safe='')}", headers=headers, method="GET")
        with urlopen(request, timeout=15) as response:
            current = json.loads(response.read().decode("utf-8"))
            sha = current.get("sha")
    except HTTPError as exc:
        if exc.code != 404:
            detail = exc.read().decode("utf-8", errors="replace")[:600]
            raise ContentError(f"GitHub lookup failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise ContentError(f"Could not reach GitHub: {exc.reason}") from exc

    serialized = (json.dumps(canonicalise_site(content), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    body: dict[str, Any] = {
        "message": message[:250],
        "content": base64.b64encode(serialized).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    request = Request(
        base_url,
        data=json.dumps(body).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise ContentError(f"GitHub backup failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise ContentError(f"Could not reach GitHub: {exc.reason}") from exc

    commit = result.get("commit") or {}
    return {
        "ok": True,
        "path": target_path,
        "branch": branch,
        "commit_sha": commit.get("sha", ""),
    }


def portainer_is_configured() -> bool:
    return bool(os.getenv("PORTAINER_WEBHOOK_URL", "").strip())


def trigger_portainer_redeploy() -> dict[str, Any]:
    url = os.getenv("PORTAINER_WEBHOOK_URL", "").strip()
    if not url:
        raise ContentError("Portainer redeploy is not configured on the server.")
    if not url.lower().startswith("https://") and not url.lower().startswith("http://"):
        raise ContentError("PORTAINER_WEBHOOK_URL is not a valid HTTP(S) URL.")

    request = Request(
        url,
        data=b"",
        headers={"User-Agent": "stmina-hymns-school-content-publisher"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            status = response.status
            body = response.read(400).decode("utf-8", errors="replace")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise ContentError(f"Portainer redeploy failed ({exc.code}): {detail}") from exc
    except URLError as exc:
        raise ContentError(f"Could not reach Portainer: {exc.reason}") from exc

    return {"ok": 200 <= status < 300, "status": status, "response": body}
