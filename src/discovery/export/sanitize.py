"""Cell and quote cleanup for exports (EXP-01, EXP-02, EXP-03, EXP-07, PRIV-04)."""

from __future__ import annotations

import re

CELL_CHAR_LIMIT = 50_000
_FORMULA = ("=", "+", "-", "@", "\t", "\r")
_NAME = re.compile(
    r"\b(my|our)\s+"
    r"(sister|brother|mom|mother|dad|father|wife|husband|son|daughter|friend|colleague|aunt|uncle)"
    r"\s+([A-Z][a-z]+)\b"
)


def redact_names(text: str) -> str:
    """'my sister Priya' becomes 'my sister [name]' in external exports."""
    return _NAME.sub(lambda match: f"{match.group(1)} {match.group(2)} [name]", text or "")


def truncate_cell(text: str, limit: int = CELL_CHAR_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 14].rstrip() + " [truncated]"


def spreadsheet_cell(value: object) -> str:
    """Text safe to open in Excel or Google Sheets.

    A leading =, +, -, or @ is prefixed so the cell is not executed as a formula.
    Long text is truncated so it fits a Sheets cell.
    """
    if value is None:
        return ""
    text = truncate_cell(str(value))
    if text.startswith(_FORMULA):
        return "'" + text
    return text


def winansi_safe(text: str) -> str:
    """Replace glyphs Helvetica cannot draw (emoji, Devanagari) with a box."""
    out = []
    for char in text or "":
        if char in "\n\t":
            out.append(char)
            continue
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            out.append("□")
        else:
            out.append(char)
    return "".join(out)
