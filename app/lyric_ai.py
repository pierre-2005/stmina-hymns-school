from __future__ import annotations

import json
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .content_loader import ContentError

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_LYRIC_MODEL = "gpt-5.6-luna"
MAX_LANGUAGES = 8
MAX_STANZAS_PER_LANGUAGE = 300
MAX_STANZA_CHARS = 8000
MAX_TOTAL_CHARS = 240_000


def _openai_api_key() -> str:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        raise ContentError(
            "AI lyric alignment is not configured. Add OPENAI_API_KEY to the website container environment."
        )
    return key


def lyric_model() -> str:
    return os.getenv("OPENAI_LYRIC_MODEL", DEFAULT_LYRIC_MODEL).strip() or DEFAULT_LYRIC_MODEL


def ai_alignment_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def normalise_alignment_request(
    hymn_title: str,
    languages: Any,
) -> tuple[str, list[dict[str, Any]]]:
    title = str(hymn_title or "").strip()[:300]
    if not isinstance(languages, list) or not languages:
        raise ContentError("Send at least one language to the lyric aligner.")
    if len(languages) > MAX_LANGUAGES:
        raise ContentError(f"The lyric aligner supports at most {MAX_LANGUAGES} languages at once.")

    cleaned: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    total_chars = 0

    for raw in languages:
        if not isinstance(raw, dict):
            raise ContentError("Each lyric language must be a JSON object.")

        code = str(raw.get("code", "")).strip()
        name = str(raw.get("name", code)).strip() or code
        stanzas = raw.get("stanzas")

        if not code or len(code) > 40:
            raise ContentError("Each lyric language must have a valid language code.")
        if code in seen_codes:
            raise ContentError(f"Duplicate lyric language code: {code}")
        seen_codes.add(code)

        if not isinstance(stanzas, list):
            raise ContentError(f"Lyrics for {name} must be a list of stanzas.")
        if len(stanzas) > MAX_STANZAS_PER_LANGUAGE:
            raise ContentError(
                f"{name} has too many stanzas for one AI alignment request "
                f"(maximum {MAX_STANZAS_PER_LANGUAGE})."
            )

        cleaned_stanzas: list[str] = []
        for stanza in stanzas:
            text = str(stanza or "").strip()
            if len(text) > MAX_STANZA_CHARS:
                raise ContentError(
                    f"A {name} stanza is too long for AI alignment "
                    f"(maximum {MAX_STANZA_CHARS} characters)."
                )
            cleaned_stanzas.append(text)
            total_chars += len(text)

        cleaned.append({"code": code, "name": name[:120], "stanzas": cleaned_stanzas})

    if total_chars <= 0:
        raise ContentError("Paste lyrics before using AI alignment.")
    if total_chars > MAX_TOTAL_CHARS:
        raise ContentError(
            "This hymn is too large for one AI alignment request. Split it into smaller sections first."
        )

    nonempty_languages = [item for item in cleaned if item["stanzas"]]
    if len(nonempty_languages) < 2:
        raise ContentError("AI alignment needs lyrics in at least two languages.")

    return title, cleaned


def _alignment_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "parts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "code": {"type": "string"},
                                    "indexes": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                    },
                                },
                                "required": ["code", "indexes"],
                                "additionalProperties": False,
                            },
                        },
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "note": {"type": "string"},
                    },
                    "required": ["parts", "confidence", "note"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }


def _prompt_for_alignment(title: str, languages: list[dict[str, Any]]) -> str:
    codes = [item["code"] for item in languages]
    sections: list[str] = []

    for language in languages:
        lines = [f"LANGUAGE {language['code']} ({language['name']}):"]
        if language["stanzas"]:
            for index, stanza in enumerate(language["stanzas"], start=1):
                lines.append(f"[{index}] {stanza}")
        else:
            lines.append("[NO STANZAS PROVIDED]")
        sections.append("\n".join(lines))

    return (
        f"Hymn title: {title or 'Untitled hymn'}\n\n"
        "Align the numbered hymn stanzas across the supplied languages.\n"
        "Return correspondence only; never rewrite, correct, translate, normalize, or reproduce the hymn text.\n"
        "A row is one website lyric segment. A row may reference one or more consecutive source stanzas "
        "from a language when formatting differs between languages.\n"
        "For every row, include exactly one part for every language code. Use an empty indexes array when "
        "that language has no corresponding stanza in that row.\n"
        "Every source stanza index that exists must be used exactly once across all rows for its language.\n"
        "Keep source order strictly increasing. Never duplicate, skip, or reorder source stanza indexes.\n"
        "Confidence is 0 to 1. Use a short note only when the alignment needs human review; otherwise use an empty note.\n"
        f"The language codes are: {', '.join(codes)}.\n\n"
        + "\n\n".join(sections)
    )


def _extract_response_text(payload: dict[str, Any]) -> str:
    pieces: list[str] = []
    refusal = ""

    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            kind = content.get("type")
            if kind == "output_text":
                pieces.append(str(content.get("text", "")))
            elif kind == "refusal":
                refusal = str(content.get("refusal", "")).strip()

    text = "".join(pieces).strip()
    if text:
        return text
    if refusal:
        raise ContentError(f"The AI lyric aligner refused the request: {refusal}")
    raise ContentError("The AI lyric aligner returned no alignment data.")


def _validate_alignment(
    raw_alignment: Any,
    languages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_alignment, dict) or not isinstance(raw_alignment.get("rows"), list):
        raise ContentError("The AI lyric aligner returned an invalid response shape.")

    codes = [item["code"] for item in languages]
    counts = {item["code"]: len(item["stanzas"]) for item in languages}
    used: dict[str, list[int]] = {code: [] for code in codes}
    validated_rows: list[dict[str, Any]] = []

    for row_number, raw_row in enumerate(raw_alignment["rows"], start=1):
        if not isinstance(raw_row, dict):
            raise ContentError(f"AI alignment row {row_number} is invalid.")

        parts = raw_row.get("parts")
        if not isinstance(parts, list):
            raise ContentError(f"AI alignment row {row_number} has no parts list.")

        row_map: dict[str, list[int]] = {}
        for part in parts:
            if not isinstance(part, dict):
                raise ContentError(f"AI alignment row {row_number} contains an invalid language part.")
            code = str(part.get("code", ""))
            indexes = part.get("indexes")
            if code not in counts:
                raise ContentError(f"AI alignment row {row_number} returned unknown language code '{code}'.")
            if code in row_map:
                raise ContentError(f"AI alignment row {row_number} repeated language code '{code}'.")
            if not isinstance(indexes, list) or any(not isinstance(index, int) for index in indexes):
                raise ContentError(f"AI alignment row {row_number} returned invalid indexes for '{code}'.")
            if indexes != sorted(indexes) or len(indexes) != len(set(indexes)):
                raise ContentError(f"AI alignment row {row_number} returned unordered or duplicate indexes for '{code}'.")
            for index in indexes:
                if index < 1 or index > counts[code]:
                    raise ContentError(
                        f"AI alignment row {row_number} returned out-of-range {code} stanza #{index}."
                    )
            row_map[code] = indexes

        if set(row_map) != set(codes):
            missing = [code for code in codes if code not in row_map]
            raise ContentError(
                f"AI alignment row {row_number} omitted language code(s): {', '.join(missing)}."
            )
        if not any(row_map[code] for code in codes):
            raise ContentError(f"AI alignment row {row_number} is empty.")

        for code in codes:
            used[code].extend(row_map[code])

        try:
            confidence = float(raw_row.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        validated_rows.append(
            {
                "parts": [{"code": code, "indexes": row_map[code]} for code in codes],
                "confidence": confidence,
                "note": str(raw_row.get("note", "")).strip()[:500],
            }
        )

    if not validated_rows:
        raise ContentError("The AI lyric aligner returned no rows.")

    for code in codes:
        expected = list(range(1, counts[code] + 1))
        if used[code] != expected:
            raise ContentError(
                f"The AI alignment did not preserve every {code} stanza exactly once and in order. "
                "Try the alignment again or split the hymn into a smaller section."
            )

    return validated_rows


def align_lyrics_with_openai(
    hymn_title: str,
    languages: Any,
) -> dict[str, Any]:
    title, cleaned_languages = normalise_alignment_request(hymn_title, languages)
    model = lyric_model()

    request_body = {
        "model": model,
        "instructions": (
            "You align parallel multilingual hymn stanzas. You must only return stanza-index relationships. "
            "Never rewrite or output the hymn text itself. Follow the requested JSON schema exactly."
        ),
        "input": _prompt_for_alignment(title, cleaned_languages),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "stmina_hymn_lyric_alignment",
                "strict": True,
                "schema": _alignment_schema(),
            }
        },
        "max_output_tokens": 8000,
        "store": False,
    }

    encoded = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    request = Request(
        OPENAI_RESPONSES_URL,
        data=encoded,
        method="POST",
        headers={
            "Authorization": f"Bearer {_openai_api_key()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "stmina-hymns-school/lyric-aligner",
        },
    )

    try:
        with urlopen(request, timeout=90) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail = raw
        try:
            parsed = json.loads(raw)
            error = parsed.get("error") if isinstance(parsed, dict) else None
            if isinstance(error, dict):
                detail = str(error.get("message") or raw)
        except Exception:
            pass
        raise ContentError(f"OpenAI lyric alignment failed: {detail}") from exc
    except URLError as exc:
        raise ContentError(f"Could not reach OpenAI for lyric alignment: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ContentError("OpenAI lyric alignment timed out. Try again.") from exc
    except json.JSONDecodeError as exc:
        raise ContentError("OpenAI returned an invalid API response.") from exc

    raw_text = _extract_response_text(response_payload)
    try:
        raw_alignment = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ContentError("OpenAI returned alignment data that was not valid JSON.") from exc

    rows = _validate_alignment(raw_alignment, cleaned_languages)
    return {
        "rows": rows,
        "model": model,
        "language_counts": {item["code"]: len(item["stanzas"]) for item in cleaned_languages},
    }
