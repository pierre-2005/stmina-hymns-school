from __future__ import annotations

import csv
import io
import shutil
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class OcrError(RuntimeError):
    """Raised when local hymn OCR cannot be completed safely."""


@dataclass
class OcrLine:
    block: int
    paragraph: int
    line: int
    top: int
    bottom: int
    left: int
    text: str

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


_ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def ocr_available() -> bool:
    return bool(shutil.which("tesseract"))


def _safe_suffix(filename: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    return suffix if suffix in _ALLOWED_SUFFIXES else ".png"


def _run_tesseract(image_bytes: bytes, filename: str) -> str:
    executable = shutil.which("tesseract")
    if not executable:
        raise OcrError(
            "Local OCR is not installed on the website server. Redeploy the website "
            "using the updated Dockerfile so Tesseract OCR is installed."
        )

    if not image_bytes:
        raise OcrError("The selected image was empty.")

    suffix = _safe_suffix(filename)
    temp_path = ""

    try:
        with tempfile.NamedTemporaryFile(prefix="stmina-ocr-", suffix=suffix, delete=False) as temp:
            temp.write(image_bytes)
            temp_path = temp.name

        # TSV gives us word positions and Tesseract paragraph numbers. That lets
        # us preserve the visible stanza spacing instead of flattening a hymn
        # screenshot into one long paragraph.
        process = subprocess.run(
            [
                executable,
                temp_path,
                "stdout",
                "-l",
                "eng",
                "--psm",
                "6",
                "tsv",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError("OCR took too long. Try a smaller or clearer screenshot.") from exc
    except OSError as exc:
        raise OcrError(f"Could not run local OCR: {exc}") from exc
    finally:
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass

    if process.returncode != 0:
        error = process.stderr.decode("utf-8", errors="replace").strip()
        raise OcrError(error or "Tesseract could not read the selected image.")

    return process.stdout.decode("utf-8", errors="replace")


def _parse_tsv(tsv_text: str) -> list[OcrLine]:
    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t")
    grouped: dict[tuple[int, int, int], dict[str, object]] = {}

    for row in reader:
        try:
            if int(row.get("level", "0") or 0) != 5:
                continue
            text = str(row.get("text", "") or "").strip()
            if not text:
                continue

            block = int(row.get("block_num", "0") or 0)
            paragraph = int(row.get("par_num", "0") or 0)
            line = int(row.get("line_num", "0") or 0)
            left = int(row.get("left", "0") or 0)
            top = int(row.get("top", "0") or 0)
            width = int(row.get("width", "0") or 0)
            height = int(row.get("height", "0") or 0)
        except (TypeError, ValueError):
            continue

        key = (block, paragraph, line)
        entry = grouped.setdefault(
            key,
            {
                "block": block,
                "paragraph": paragraph,
                "line": line,
                "top": top,
                "bottom": top + height,
                "left": left,
                "words": [],
            },
        )
        entry["top"] = min(int(entry["top"]), top)
        entry["bottom"] = max(int(entry["bottom"]), top + height)
        entry["left"] = min(int(entry["left"]), left)
        words = entry["words"]
        if isinstance(words, list):
            words.append((left, text))

    lines: list[OcrLine] = []
    for entry in grouped.values():
        words = sorted(entry["words"], key=lambda item: item[0]) if isinstance(entry["words"], list) else []
        text = " ".join(word for _left, word in words).strip()
        if not text:
            continue
        lines.append(
            OcrLine(
                block=int(entry["block"]),
                paragraph=int(entry["paragraph"]),
                line=int(entry["line"]),
                top=int(entry["top"]),
                bottom=int(entry["bottom"]),
                left=int(entry["left"]),
                text=text,
            )
        )

    lines.sort(key=lambda item: (item.top, item.left, item.block, item.paragraph, item.line))
    return lines


def _paragraphs_from_tesseract(lines: list[OcrLine]) -> list[str]:
    if not lines:
        return []

    paragraph_keys: list[tuple[int, int]] = []
    groups: dict[tuple[int, int], list[OcrLine]] = {}

    for line in lines:
        key = (line.block, line.paragraph)
        if key not in groups:
            paragraph_keys.append(key)
            groups[key] = []
        groups[key].append(line)

    # Tesseract usually identifies the visible hymn stanzas as paragraphs. When
    # it does, this is the most reliable and least destructive reconstruction.
    if len(paragraph_keys) > 1 and any(key[1] > 1 for key in paragraph_keys):
        return [
            " ".join(item.text for item in sorted(groups[key], key=lambda value: (value.top, value.left))).strip()
            for key in paragraph_keys
            if groups[key]
        ]

    # Fallback for screenshots where Tesseract reports everything as one
    # paragraph: use the vertical whitespace between OCR lines. A stanza break
    # is normally substantially larger than ordinary wrapped-line spacing.
    heights = [line.height for line in lines]
    median_height = statistics.median(heights) if heights else 30
    gap_threshold = max(10.0, float(median_height) * 0.34)

    paragraphs: list[list[str]] = [[lines[0].text]]
    previous = lines[0]

    for line in lines[1:]:
        gap = line.top - previous.bottom
        if gap > gap_threshold:
            paragraphs.append([])
        paragraphs[-1].append(line.text)
        previous = line

    return [" ".join(parts).strip() for parts in paragraphs if parts]


def extract_english_hymn_text(image_bytes: bytes, filename: str = "image.png") -> dict[str, object]:
    """
    Extract English hymn text from a screenshot using local Tesseract OCR.

    Returns text with a blank line between visually detected stanzas so the
    desktop manager's existing bulk stanza parser can consume it directly.
    """
    tsv_text = _run_tesseract(image_bytes, filename)
    lines = _parse_tsv(tsv_text)
    paragraphs = [value for value in _paragraphs_from_tesseract(lines) if value]

    if not paragraphs:
        raise OcrError(
            "No readable English text was detected. Try a clearer screenshot with larger text."
        )

    return {
        "text": "\n\n".join(paragraphs),
        "stanzas": len(paragraphs),
        "lines": len(lines),
        "engine": "tesseract",
    }
