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


def contains_unicode_coptic(text: str) -> bool:
    for ch in str(text or ""):
        cp = ord(ch)

        if (
            0x2C80 <= cp <= 0x2CFF
            or 0x03E2 <= cp <= 0x03EF
        ):
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
        previous_kind, previous_value = runs[-1]
        runs[-1] = (
            previous_kind,
            previous_value + value,
        )
    else:
        runs.append((kind, value))


def unicode_coptic_to_runs(
    value: str,
) -> list[tuple[str, str]]:
    """
    Convert Unicode Coptic into Avva legacy characters while
    preserving ordinary punctuation/text as separate plain runs.

    Example:

        Unicode Ⲑ -> ")" using Avva font
        literal  ) -> ")" using normal website font

    This removes the ambiguity that exists when everything is
    stored as one legacy Avva string.
    """

    text = unicodedata.normalize(
        "NFD",
        str(value or ""),
    )

    runs: list[tuple[str, str]] = []

    i = 0

    while i < len(text):

        # Dedicated Avva combinations.
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

        # Actual Unicode Coptic character.
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

            for mark in combining_marks:

                # Avva Shenouda grave mark.
                if mark == "\u0300":
                    prefix += "`"

                else:
                    suffix += mark

            _append_run(
                runs,
                "avva",
                prefix + mapped + suffix,
            )

            i = j
            continue

        # Anything that is NOT Coptic stays normal:
        #
        # ( )
        # [ ]
        # { }
        # ...
        # punctuation
        # numbers
        # English
        # spaces
        # line breaks
        _append_run(
            runs,
            "plain",
            ch,
        )

        i += 1

    return runs


def _html_text(text: str) -> str:
    return str(
        escape(text)
    ).replace(
        "\n",
        "<br>",
    )


def render_coptic(value: str) -> Markup:
    """
    Render Coptic safely.

    New content:
        Unicode Coptic is converted into mixed Avva/plain spans.

    Old content:
        Existing legacy Avva text still renders normally so the
        upgrade does not immediately break older hymns.
    """

    text = str(value or "")

    if not text:
        return Markup("")

    # Backward compatibility for existing Avva-encoded content.
    if not contains_unicode_coptic(text):
        return Markup(
            '<span class="coptic-avva">'
            + _html_text(text)
            + "</span>"
        )

    parts: list[str] = []

    for kind, chunk in unicode_coptic_to_runs(text):

        if kind == "avva":
            css_class = "coptic-avva"
        else:
            css_class = "coptic-plain"

        parts.append(
            f'<span class="{css_class}">'
            f'{_html_text(chunk)}'
            f'</span>'
        )

    return Markup("".join(parts))