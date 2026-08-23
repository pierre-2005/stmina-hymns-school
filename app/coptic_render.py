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

OVERLINE_MARKS = {
    "\u0304",  # COMBINING MACRON
    "\u0305",  # COMBINING OVERLINE
    "\u033F",  # COMBINING DOUBLE OVERLINE
}

AVVA_GLYPHS = set(UNICODE_TO_AVVA.values()) | set(SPECIAL_SEQUENCES.values())


def contains_unicode_coptic(text: str) -> bool:
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
    """
    Merge adjacent runs of the same kind.

    This is deliberate for overlined text: if several consecutive Coptic
    letters are marked, they become ONE span and therefore ONE continuous,
    uniform line — matching the Content Manager preview much more closely.
    """
    if not value:
        return

    if runs and runs[-1][0] == kind:
        runs[-1] = (kind, runs[-1][1] + value)
    else:
        runs.append((kind, value))


def unicode_coptic_to_runs(text: str) -> list[tuple[str, str]]:
    """
    Convert Unicode Coptic into mixed rendering runs.

    - Coptic letters -> Avva Shenouda legacy glyphs
    - literal punctuation -> normal website font
    - grave accent -> Avva legacy backtick before the base glyph
    - macron/overline marks -> CSS overline, never Unicode combining marks

    Removing the combining overline before browser rendering avoids the
    fallback-font problem that exposed Avva's legacy "3" as a literal digit.
    """
    text = unicodedata.normalize("NFD", str(text or ""))
    runs: list[tuple[str, str]] = []
    i = 0

    while i < len(text):
        # Dedicated original-converter combinations take precedence.
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
            marks: list[str] = []

            while j < len(text) and unicodedata.combining(text[j]):
                marks.append(text[j])
                j += 1

            prefix = ""
            suffix = ""
            has_overline = False

            for mark in marks:
                if mark == "\u0300":
                    prefix += "`"
                elif mark in OVERLINE_MARKS:
                    has_overline = True
                else:
                    suffix += mark

            _append_run(
                runs,
                "avva-overline" if has_overline else "avva",
                prefix + mapped + suffix,
            )

            i = j
            continue

        # Never show an unattached formatting mark as a floating dash.
        if ch in OVERLINE_MARKS:
            i += 1
            continue

        _append_run(runs, "plain", ch)
        i += 1

    return runs


def legacy_avva_to_runs(text: str) -> list[tuple[str, str]]:
    """
    Backward compatibility for older legacy-Avva rows.

    Matched (), [], and {} remain literal normal text.
    Legacy Avva glyph + overline mark becomes one CSS-overlined run.
    """
    text = unicodedata.normalize("NFD", str(text or ""))
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

            if ch in OVERLINE_MARKS:
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

        if (
            ch == "`"
            and i + 1 < len(text)
            and text[i + 1] in AVVA_GLYPHS
        ):
            value = ch + text[i + 1]

            if (
                i + 2 < len(text)
                and text[i + 2] in OVERLINE_MARKS
            ):
                _append_run(runs, "avva-overline", value)
                i += 3
            else:
                _append_run(runs, "avva", value)
                i += 2

            continue

        if ch in AVVA_GLYPHS:
            if (
                i + 1 < len(text)
                and text[i + 1] in OVERLINE_MARKS
            ):
                _append_run(runs, "avva-overline", ch)
                i += 2
            else:
                _append_run(runs, "avva", ch)
                i += 1

            continue

        if ch in OVERLINE_MARKS:
            i += 1
            continue

        _append_run(runs, "plain", ch)
        i += 1

    return runs


def _escape_with_breaks(value: str) -> str:
    return str(escape(value)).replace("\n", "<br>")


def render_coptic(value: str) -> Markup:
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

        if kind == "avva-overline":
            html.append(
                '<span '
                'class="coptic-avva coptic-overline" '
                'style="font-family:'
                '\'Avva Shenouda\','
                '\'Noto Sans Coptic\','
                'sans-serif !important;'
                'font-weight:400;'
                'letter-spacing:0;">'
                f"{safe_chunk}"
                "</span>"
            )

        elif kind == "avva":
            html.append(
                '<span '
                'class="coptic-avva" '
                'style="font-family:'
                '\'Avva Shenouda\','
                '\'Noto Sans Coptic\','
                'sans-serif !important;'
                'font-weight:400;'
                'letter-spacing:0;">'
                f"{safe_chunk}"
                "</span>"
            )

        else:
            html.append(
                '<span '
                'class="coptic-plain" '
                'style="font-family:'
                'Inter,'
                'ui-sans-serif,'
                'system-ui,'
                '-apple-system,'
                'BlinkMacSystemFont,'
                '\'Segoe UI\','
                'Arial,'
                'sans-serif !important;'
                'font-weight:400;'
                'letter-spacing:0;">'
                f"{safe_chunk}"
                "</span>"
            )

    return Markup("".join(html))
