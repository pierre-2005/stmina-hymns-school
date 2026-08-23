from __future__ import annotations

import unicodedata

from markupsafe import Markup, escape


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

AVVA_GLYPHS = set(UNICODE_TO_AVVA.values()) | set(SPECIAL_SEQUENCES.values())


def contains_unicode_coptic(text: str) -> bool:
    """Return True when text contains actual Unicode Coptic characters."""
    for ch in str(text or ""):
        cp = ord(ch)
        if 0x2C80 <= cp <= 0x2CFF or 0x03E2 <= cp <= 0x03EF:
            return True
    return False


def _append_run(
    runs: list[tuple[str, str]],
    kind: str,
    value: str,
) -> None:
    if not value:
        return

    if runs and runs[-1][0] == kind:
        runs[-1] = (kind, runs[-1][1] + value)
    else:
        runs.append((kind, value))


def unicode_coptic_to_runs(text: str) -> list[tuple[str, str]]:
    """
    Convert actual Unicode Coptic into mixed rendering runs.

    Only real Unicode Coptic characters are converted to Avva legacy glyph
    codes. Literal punctuation, brackets, English, ordinary numbers, spaces,
    and line breaks remain plain.

    Example:
        Unicode Ⲑ -> ")" in an Avva span
        literal  ) -> ")" in a normal-font span
    """
    text = unicodedata.normalize("NFD", str(text or ""))
    runs: list[tuple[str, str]] = []
    i = 0

    while i < len(text):
        if i + 1 < len(text):
            pair = text[i:i + 2]
            if pair in SPECIAL_SEQUENCES:
                _append_run(runs, "avva", SPECIAL_SEQUENCES[pair])
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
                    # Avva Shenouda expects its grave mark BEFORE the base glyph.
                    prefix += "`"
                else:
                    suffix += mark

            _append_run(runs, "avva", prefix + mapped + suffix)
            i = j
            continue

        _append_run(runs, "plain", ch)
        i += 1

    return runs


def legacy_avva_to_runs(text: str) -> list[tuple[str, str]]:
    """
    Backward compatibility for older rows that were saved as legacy Avva text.

    Legacy Avva is ambiguous because ASCII positions are reused as Coptic
    glyphs. Matched (), [], and {} regions are therefore treated as literal
    normal text, including everything inside them.

    New Content Manager saves use Unicode and do not have this ambiguity.
    """
    text = str(text or "")
    runs: list[tuple[str, str]] = []

    opening_to_closing = {
        "(": ")",
        "[": "]",
        "{": "}",
    }
    expected_closers: list[str] = []
    i = 0

    while i < len(text):
        ch = text[i]

        if expected_closers:
            if ch in opening_to_closing:
                expected_closers.append(opening_to_closing[ch])
                _append_run(runs, "plain", ch)
                i += 1
                continue

            if ch == expected_closers[-1]:
                expected_closers.pop()
                _append_run(runs, "plain", ch)
                i += 1
                continue

            _append_run(runs, "plain", ch)
            i += 1
            continue

        if ch in opening_to_closing:
            expected_closers.append(opening_to_closing[ch])
            _append_run(runs, "plain", ch)
            i += 1
            continue

        if ch == "`" and i + 1 < len(text) and text[i + 1] in AVVA_GLYPHS:
            _append_run(runs, "avva", ch + text[i + 1])
            i += 2
            continue

        if ch in AVVA_GLYPHS:
            _append_run(runs, "avva", ch)
        else:
            _append_run(runs, "plain", ch)

        i += 1

    return runs


def _escape_with_breaks(value: str) -> str:
    return str(escape(value)).replace("\n", "<br>")


def render_coptic(value: str) -> Markup:
    """
    Safely render Coptic + ordinary punctuation using explicit mixed fonts.

    Inline font declarations are intentional: even if a browser still has an
    older cached stylesheet that puts Avva Shenouda on the whole Coptic cell,
    literal (...) remains in the normal UI font.
    """
    text = str(value or "")
    if not text:
        return Markup("")

    runs = (
        unicode_coptic_to_runs(text)
        if contains_unicode_coptic(text)
        else legacy_avva_to_runs(text)
    )

    html: list[str] = []

    for kind, chunk in runs:
        safe_chunk = _escape_with_breaks(chunk)

        if kind == "avva":
            html.append(
                '<span class="coptic-avva" '
                'style="font-family:\'Avva Shenouda\',\'Noto Sans Coptic\',sans-serif !important;'
                'font-weight:400;letter-spacing:0;">'
                f"{safe_chunk}"
                "</span>"
            )
        else:
            html.append(
                '<span class="coptic-plain" '
                'style="font-family:Inter,ui-sans-serif,system-ui,-apple-system,'
                'BlinkMacSystemFont,\'Segoe UI\',Arial,sans-serif !important;'
                'font-weight:400;letter-spacing:0;">'
                f"{safe_chunk}"
                "</span>"
            )

    return Markup("".join(html))
