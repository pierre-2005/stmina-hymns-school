from __future__ import annotations

import unicodedata

from markupsafe import Markup, escape


# ---------------------------------------------------------------------------
# Unicode Coptic -> Avva Shenouda legacy glyph mapping
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


# These two exact combinations are part of the original Avva converter and
# have dedicated legacy glyph positions in the Avva Shenouda font.
SPECIAL_SEQUENCES = {
    "ⲇ\u0305": "ä",
    "ⲩ\u0305": "ö",
}


# Unicode marks commonly used as Coptic abbreviation / nomina-sacra lines.
OVERLINE_MARKS = {
    "\u0304",  # COMBINING MACRON
    "\u0305",  # COMBINING OVERLINE
    "\u033F",  # COMBINING DOUBLE OVERLINE
}


AVVA_GLYPHS = set(UNICODE_TO_AVVA.values()) | set(SPECIAL_SEQUENCES.values())


# ---------------------------------------------------------------------------
# IMPORTANT: exact Avva Shenouda vertical metrics
# ---------------------------------------------------------------------------
#
# The previous CSS fix used one fixed vertical position for every overline.
# That cannot work with Avva Shenouda because its legacy glyphs have very
# different ink heights:
#
#   lowercase eta "3"  -> short glyph
#   uppercase eta "#"  -> tall glyph
#   several other letters have medium/tall ascenders
#
# These values were calculated from the actual Avva_Shenouda.ttf used by the
# project. They represent the CSS `top` position for the overline when the
# wrapper has line-height: 1.
#
# This lets the line sit just above the ACTUAL glyph rather than floating at a
# fixed distance above the whole line box.
#
LEGACY_OVERLINE_TOP_EM = {
    # Uppercase Avva legacy glyphs
    "A": 0.003,
    "B": 0.006,
    "J": 0.002,
    "D": 0.002,
    "E": 0.000,
    "Z": 0.002,
    "#": 0.002,
    ")": 0.002,
    "I": 0.004,
    "K": 0.002,
    "L": 0.002,
    "M": 0.002,
    "N": 0.002,
    "&": 0.005,
    "O": 0.002,
    "P": 0.001,
    "R": 0.002,
    "C": 0.004,
    "T": 0.001,
    "V": 0.002,
    "F": 0.002,
    "X": 0.002,
    "Y": 0.002,
    "W": 0.002,
    "@": 0.002,
    "$": 0.002,
    "Q": 0.007,
    "H": 0.002,
    "G": 0.002,
    "S": 0.002,
    "%": -0.050,

    # Lowercase Avva legacy glyphs
    "a": 0.254,
    "b": 0.104,
    "j": 0.259,
    "d": 0.106,
    "e": 0.260,
    "z": 0.261,
    "3": 0.258,  # lowercase eta
    "0": 0.258,
    "i": 0.256,
    "k": 0.260,
    "l": 0.109,
    "m": 0.256,
    "n": 0.259,
    "7": 0.262,
    "o": 0.256,
    "p": 0.260,
    "r": 0.258,
    "c": 0.257,
    "t": 0.258,
    "v": 0.260,
    "f": 0.117,
    "x": 0.251,
    "y": 0.118,
    "w": 0.250,
    "2": 0.255,
    "4": 0.258,
    "q": 0.116,
    "h": 0.261,
    "g": 0.258,
    "s": -0.015,
    "5": 0.002,
    "6": 0.123,
    "U": -0.050,
    "u": -0.018,
    "+": -0.050,
}


def contains_unicode_coptic(text: str) -> bool:
    """Return True if text contains actual Unicode Coptic characters."""
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
    Add a rendering run.

    Overlined glyphs are intentionally NOT merged. Each glyph needs its own
    Avva-metric-based vertical position.
    """
    if not value:
        return

    if kind == "avva-overline":
        runs.append((kind, value))
        return

    if runs and runs[-1][0] == kind:
        runs[-1] = (kind, runs[-1][1] + value)
    else:
        runs.append((kind, value))


def unicode_coptic_to_runs(text: str) -> list[tuple[str, str]]:
    """
    Convert Unicode Coptic into rendering runs.

    - Actual Coptic characters -> Avva legacy glyphs.
    - Normal punctuation -> normal website font.
    - Grave marks -> Avva's legacy backtick.
    - Overline/macron marks -> CSS overline metadata, NOT Unicode marks.

    The browser never receives U+0304/U+0305/U+033F beside a legacy Avva ASCII
    glyph, so it cannot expose a literal "3", "m", "n", etc. through font
    fallback.
    """
    text = unicodedata.normalize("NFD", str(text or ""))
    runs: list[tuple[str, str]] = []
    i = 0

    while i < len(text):

        # Keep exact dedicated combinations from the original converter.
        if i + 1 < len(text):
            pair = text[i:i + 2]

            if pair in SPECIAL_SEQUENCES:
                _append_run(
                    runs,
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
                combining_marks.append(text[j])
                j += 1

            prefix = ""
            suffix = ""
            has_overline = False

            for mark in combining_marks:

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

        # Never display a detached formatting mark as a random floating dash.
        if ch in OVERLINE_MARKS:
            i += 1
            continue

        _append_run(
            runs,
            "plain",
            ch,
        )
        i += 1

    return runs


def legacy_avva_to_runs(text: str) -> list[tuple[str, str]]:
    """
    Backward compatibility for older legacy-Avva rows.

    - (...) / [...] / {...} remain literal normal text.
    - Legacy glyph + overline becomes avva-overline.
    - Detached overline/macron marks are removed.
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
                expected_closers.append(
                    opening_to_closing[ch]
                )
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
            expected_closers.append(
                opening_to_closing[ch]
            )
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
                _append_run(
                    runs,
                    "avva-overline",
                    value,
                )
                i += 3
            else:
                _append_run(
                    runs,
                    "avva",
                    value,
                )
                i += 2

            continue

        if ch in AVVA_GLYPHS:

            if (
                i + 1 < len(text)
                and text[i + 1] in OVERLINE_MARKS
            ):
                _append_run(
                    runs,
                    "avva-overline",
                    ch,
                )
                i += 2
            else:
                _append_run(
                    runs,
                    "avva",
                    ch,
                )
                i += 1

            continue

        if ch in OVERLINE_MARKS:
            i += 1
            continue

        _append_run(
            runs,
            "plain",
            ch,
        )
        i += 1

    return runs


def _escape_with_breaks(value: str) -> str:
    return str(
        escape(value)
    ).replace(
        "\n",
        "<br>",
    )


def _legacy_base_character(chunk: str) -> str:
    """
    Return the actual Avva base glyph from a legacy chunk.

    Grave-accented text is stored as backtick + base, e.g. `e.
    """
    for ch in reversed(chunk):
        if ch in LEGACY_OVERLINE_TOP_EM:
            return ch

    return ""


def _overline_top_for_chunk(chunk: str) -> float:
    base = _legacy_base_character(chunk)

    if not base:
        return 0.240

    return LEGACY_OVERLINE_TOP_EM.get(
        base,
        0.240,
    )


def render_coptic(value: str) -> Markup:
    """
    Render Coptic safely using the ACTUAL Avva glyph height for each overline.

    This fixes the previous fixed-position CSS line that floated far above
    short glyphs or crossed tall glyphs.
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
        safe_chunk = _escape_with_breaks(
            chunk
        )

        if kind == "avva-overline":
            top = _overline_top_for_chunk(
                chunk
            )

            html.append(
                '<span '
                'class="coptic-avva coptic-overline" '
                f'style="--coptic-overline-top:{top:.3f}em;'
                'font-family:'
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

    return Markup(
        "".join(html)
    )
