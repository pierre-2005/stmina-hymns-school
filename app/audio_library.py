from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from array import array
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, urlparse


class AudioLibraryError(RuntimeError):
    """Raised when a managed hymn recording cannot be created or removed safely."""


def upload_root() -> Path:
    return Path(os.getenv("UPLOAD_DIR", "/app/uploads"))


def hymn_audio_root() -> Path:
    root = upload_root() / "hymn-audio"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _max_audio_bytes() -> int:
    try:
        mb = int(os.getenv("HYMN_AUDIO_MAX_MB", os.getenv("MAX_UPLOAD_MB", "40")))
    except ValueError:
        mb = 40
    return max(5, min(500, mb)) * 1024 * 1024


def _max_processed_bytes() -> int:
    try:
        mb = int(os.getenv("HYMN_AUDIO_MAX_PROCESSED_MB", "250"))
    except ValueError:
        mb = 250
    return max(20, min(1000, mb)) * 1024 * 1024


def _run(
    command: list[str],
    *,
    timeout: int = 300,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            check=True,
            stdout=subprocess.PIPE if capture_output else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AudioLibraryError(
            f"Required audio tool '{command[0]}' is not installed in the website container."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioLibraryError("Audio processing took too long and was stopped.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        if len(detail) > 1400:
            detail = detail[-1400:]
        raise AudioLibraryError(detail or "Audio processing failed.") from exc


def _safe_audio_path(filename: str) -> Path:
    name = Path(str(filename or "")).name
    if not name or name != str(filename or "") or not name.lower().endswith(".mp3"):
        raise AudioLibraryError("Invalid self-hosted audio filename.")
    root = hymn_audio_root().resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise AudioLibraryError("Invalid self-hosted audio path.")
    return path


def audio_file_exists(filename: str) -> bool:
    try:
        return _safe_audio_path(filename).is_file()
    except AudioLibraryError:
        return False


def public_audio_url(filename: str) -> str:
    _safe_audio_path(filename)
    # Same-origin URL keeps HTML5 audio seeking/streaming simple and survives domain changes.
    return f"/api/content/audio/file/{quote(filename, safe='')}"


def _probe_duration_ms(path: Path) -> int:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    try:
        parsed = json.loads(result.stdout.decode("utf-8"))
        duration = float(parsed.get("format", {}).get("duration", 0) or 0)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AudioLibraryError("Could not read the duration of the processed audio file.") from exc
    if duration <= 0:
        raise AudioLibraryError("The processed audio file has no readable duration.")
    return int(round(duration * 1000))


def _waveform_peaks(path: Path, buckets: int = 720) -> list[int]:
    """Return compact normalized 1..100 waveform peaks without numpy."""
    result = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "3000",
            "-f",
            "s16le",
            "-",
        ],
        timeout=360,
    )
    samples = array("h")
    usable = len(result.stdout) - (len(result.stdout) % 2)
    samples.frombytes(result.stdout[:usable])
    if not samples:
        return [8] * max(120, min(1200, int(buckets)))

    bucket_count = max(120, min(1200, int(buckets)))
    step = max(1, math.ceil(len(samples) / bucket_count))
    raw: list[int] = []
    for start in range(0, len(samples), step):
        chunk = samples[start : start + step]
        raw.append(max((abs(int(value)) for value in chunk), default=0))
        if len(raw) >= bucket_count:
            break
    while len(raw) < bucket_count:
        raw.append(0)

    maximum = max(raw) or 1
    # Compression keeps quiet hymn sections visible, similar to streaming waveform UIs.
    return [
        max(5, min(100, int(round((value / maximum) ** 0.58 * 100))))
        for value in raw
    ]


def _process_source_to_mp3(source: Path, *, title: str = "Recording") -> dict[str, Any]:
    filename = f"{uuid.uuid4().hex}.mp3"
    destination = hymn_audio_root() / filename
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(source),
                "-vn",
                "-map_metadata",
                "-1",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "128k",
                str(destination),
            ],
            timeout=900,
        )
        if not destination.exists() or destination.stat().st_size == 0:
            raise AudioLibraryError("The processed audio file was empty.")
        if destination.stat().st_size > _max_processed_bytes():
            raise AudioLibraryError(
                "The processed recording is too large for the configured hymn-audio limit."
            )

        duration_ms = _probe_duration_ms(destination)
        waveform = _waveform_peaks(destination)
        return {
            "type": "audio",
            "label": str(title or "Recording").strip() or "Recording",
            "audio_file": filename,
            "audio_url": public_audio_url(filename),
            "duration_ms": duration_ms,
            "waveform": waveform,
        }
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def import_uploaded_audio(data: bytes, filename: str) -> dict[str, Any]:
    if not data:
        raise AudioLibraryError("The selected audio file was empty.")
    max_bytes = _max_audio_bytes()
    if len(data) > max_bytes:
        raise AudioLibraryError(
            f"The audio file is larger than {max_bytes // (1024 * 1024)} MB."
        )

    suffix = Path(filename or "audio").suffix.lower()
    allowed = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".webm", ".flac"}
    if suffix not in allowed:
        raise AudioLibraryError(
            "Unsupported audio type. Use MP3, M4A, AAC, WAV, OGG, OPUS, WEBM, or FLAC."
        )

    with tempfile.TemporaryDirectory(prefix="stmina-audio-") as tmp:
        source = Path(tmp) / f"source{suffix}"
        source.write_bytes(data)
        result = _process_source_to_mp3(source, title=Path(filename).stem or "Recording")
    result["source_type"] = "upload"
    result["source_url"] = ""
    return result


def _validate_youtube_url(url: str) -> str:
    text = str(url or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError as exc:
        raise AudioLibraryError("Enter a valid YouTube URL.") from exc
    host = parsed.netloc.lower().split(":", 1)[0]
    allowed = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
    if parsed.scheme not in {"http", "https"} or host not in allowed:
        raise AudioLibraryError("Enter a normal youtube.com or youtu.be video URL.")
    return text


def import_youtube_audio(url: str) -> dict[str, Any]:
    """Import audio from a YouTube URL for content the administrator is authorized to use."""
    text = _validate_youtube_url(url)
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise AudioLibraryError("yt-dlp is not installed in the website container.") from exc

    with tempfile.TemporaryDirectory(prefix="stmina-youtube-") as tmp:
        temp_root = Path(tmp)
        output_template = str(temp_root / "source.%(ext)s")
        options = {
            "format": "bestaudio/best",
            "outtmpl": output_template,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "restrictfilenames": True,
            # Refuse unexpectedly huge source downloads before they can consume
            # excessive space on the Raspberry Pi. The processed MP3 has its own
            # separate post-conversion size check as well.
            "max_filesize": _max_processed_bytes(),
        }
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(text, download=True)
                prepared = Path(downloader.prepare_filename(info))
        except Exception as exc:
            detail = str(exc).strip()
            if len(detail) > 1400:
                detail = detail[-1400:]
            raise AudioLibraryError(
                "YouTube audio import failed. YouTube changes its playback rules often, so "
                "updating yt-dlp may occasionally be required.\n\n"
                + (detail or "Unknown yt-dlp error.")
            ) from exc

        if not prepared.exists():
            candidates = sorted(temp_root.glob("source.*"))
            if not candidates:
                raise AudioLibraryError(
                    "YouTube returned metadata but no downloadable audio file was produced."
                )
            prepared = candidates[0]

        title = str((info or {}).get("title") or "YouTube recording").strip()
        result = _process_source_to_mp3(prepared, title=title)
        result["source_type"] = "youtube"
        result["source_url"] = text
        result["youtube_id"] = str((info or {}).get("id") or "").strip()
        return result


def collect_audio_files(content: dict[str, Any] | None) -> set[str]:
    found: set[str] = set()
    for level in (content or {}).get("levels", []) or []:
        if not isinstance(level, dict):
            continue
        for year in level.get("years", []) or []:
            if not isinstance(year, dict):
                continue
            for hymn in year.get("hymns", []) or []:
                if not isinstance(hymn, dict):
                    continue
                for recording in hymn.get("recordings", []) or []:
                    if not isinstance(recording, dict):
                        continue
                    if str(recording.get("type", "soundcloud")).strip().lower() != "audio":
                        continue
                    name = str(recording.get("audio_file", "")).strip()
                    if not name:
                        continue
                    try:
                        _safe_audio_path(name)
                    except AudioLibraryError:
                        continue
                    found.add(name)
    return found


def delete_audio_files(filenames: Iterable[str]) -> int:
    removed = 0
    for filename in set(filenames):
        try:
            path = _safe_audio_path(filename)
        except AudioLibraryError:
            continue
        try:
            existed = path.exists()
            path.unlink(missing_ok=True)
            if existed:
                removed += 1
        except OSError as exc:
            print(f"[stminahs] Could not delete hymn audio {path}: {exc}")
    return removed


def delete_audio_if_unpublished(filename: str, published_content: dict[str, Any]) -> bool:
    """Delete a newly imported draft file only when live content does not reference it."""
    name = str(filename or "").strip()
    if not name or name in collect_audio_files(published_content):
        return False
    return bool(delete_audio_files({name}))


def cleanup_orphan_audio(keep: set[str], *, grace_seconds: int = 6 * 3600) -> int:
    """Clean abandoned imports without racing a manager draft that has not been published yet."""
    removed = 0
    now = time.time()
    root = hymn_audio_root()
    for path in root.glob("*.mp3"):
        if path.name in keep:
            continue
        try:
            if now - path.stat().st_mtime < grace_seconds:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            print(f"[stminahs] Could not clean orphan hymn audio {path}: {exc}")
    return removed
