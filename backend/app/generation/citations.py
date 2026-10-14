"""Pure text helpers for verification: citation markers, sentence units and numbers.

Only the standard library is used, so these rules are cheap to test exhaustively.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

MARKER = re.compile(r"\[\s*(C\d+(?:\s*[,;]\s*C\d+)*)\s*\]", re.IGNORECASE)
_MARKER_ID = re.compile(r"C\d+", re.IGNORECASE)
# "growth. [C1]" is rewritten to "growth [C1]." so the marker stays with its sentence.
_MARKERS_AFTER_STOP = re.compile(
    r"([.!?])((?:\s*\[\s*C\d+(?:\s*[,;]\s*C\d+)*\s*\])+)", re.IGNORECASE
)

UnitKind = Literal["heading", "lead_in", "sentence"]


@dataclass(frozen=True, slots=True)
class TextUnit:
    text: str
    kind: UnitKind


_LIST_PREFIX = re.compile(r"^(?:[-*+\u2022]|\d+[.)])\s+")
_TABLE_RULE = re.compile(r"^\|?[\s:|-]+\|?$")
_BOLD_LABEL = re.compile(r"^(\*\*|__)[^*_]+(\*\*|__):?$")
# A list marker may follow a sentence end when the model writes bullets on one line.
_BOUNDARY = re.compile(
    r"[.!?]+[\"\u201d\u2019')\]]*(\s+)(?=(?:[-*+\u2022]\s+)?[\"\u201c\u2018(*_]*[A-Z0-9])"
)
_LAST_WORD = re.compile(r"(\S+?)[.!?]+[\"\u201d\u2019')\]]*$")
ABBREVIATIONS = frozenset(
    {"e.g", "i.e", "al", "vs", "approx", "ca", "cf", "fig", "figs", "no", "dr", "prof", "resp"}
)

_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:[.\u00b7]\d+)?|\d+(?:[.\u00b7]\d+)?")
_STUDY_COUNT = re.compile(
    r"\b(\d{1,2})\s+(?:[A-Za-z-]+\s+)?"
    r"(?:studies|trials|RCTs|reviews|meta-analyses|cohorts|articles|papers|reports)\b",
    re.IGNORECASE,
)
MAX_STUDY_COUNT = 20
# gpt-oss writes non-breaking hyphens and narrow spaces ("meta\u2011analysis", "95\u202f%"),
# which would defeat every pattern written with plain characters.
_TYPOGRAPHY = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u00a0": " ",
        "\u2007": " ",
        "\u2009": " ",
        "\u202f": " ",
        "\u200b": "",
    }
)


def normalize_text(text: str) -> str:
    """Plain hyphens and spaces in place of their typographic variants."""
    return text.translate(_TYPOGRAPHY)


def normalize_marker(raw: str) -> str:
    """``"[c3]"``, ``" C3 "`` → ``"C3"``."""
    return raw.strip().strip("[]").strip().upper()


def marker_ids(text: str) -> list[str]:
    """Markers in order of first appearance, e.g. ``["C1", "C3"]``."""
    found: list[str] = []
    for match in MARKER.finditer(text):
        for marker in _MARKER_ID.findall(match.group(1)):
            normalized = marker.upper()
            if normalized not in found:
                found.append(normalized)
    return found


def marker_number(marker: str) -> int:
    return int(marker[1:])


def strip_markers(text: str) -> str:
    return " ".join(MARKER.sub("", text).split())


def normalize_marker_placement(text: str) -> str:
    return _MARKERS_AFTER_STOP.sub(lambda m: f"{m.group(2)}{m.group(1)}", text)


def text_units(text: str) -> list[TextUnit]:
    """Split markdown into headings, lead-ins (ending with ':') and sentences."""
    units: list[TextUnit] = []
    for raw in normalize_marker_placement(text).splitlines():
        line = raw.strip()
        if not line or _TABLE_RULE.match(line):
            continue
        if line.startswith("#"):
            units.append(TextUnit(line.lstrip("#").strip(), "heading"))
            continue
        if _BOLD_LABEL.match(line):
            units.append(TextUnit(line.strip("*_: "), "heading"))
            continue
        for raw_piece in _split_line(_LIST_PREFIX.sub("", line)):
            piece = _LIST_PREFIX.sub("", raw_piece)
            kind: UnitKind = "lead_in" if piece.rstrip("*_ ").endswith(":") else "sentence"
            units.append(TextUnit(piece, kind))
    return units


def sentences(text: str) -> list[str]:
    return [unit.text for unit in text_units(text) if unit.kind == "sentence"]


def _split_line(line: str) -> list[str]:
    pieces: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(line):
        piece = line[start : match.start(1)]
        word = _LAST_WORD.search(piece)
        if word and word.group(1).lstrip("(\"'\u201c\u2018").lower() in ABBREVIATIONS:
            continue
        pieces.append(piece.strip())
        start = match.end(1)
    pieces.append(line[start:].strip())
    return [piece for piece in pieces if piece]


def numbers_in(text: str) -> set[Decimal]:
    """Numeric values in ``text`` (markers excluded); '1,200', '1200' and '1200.0' are equal."""
    values: set[Decimal] = set()
    for token in _NUMBER.findall(strip_markers(text)):
        value = _to_decimal(token)
        if value is not None:
            values.add(value)
    return values


def checkable_numbers(sentence: str) -> set[Decimal]:
    """Numbers a sentence must take from its sources. Small study counts ("3 trials") are
    the writer's own tally, so they are exempt.
    """
    text = strip_markers(sentence)
    counts = {
        Decimal(m.group(1))
        for m in _STUDY_COUNT.finditer(text)
        if int(m.group(1)) <= MAX_STUDY_COUNT
    }
    return numbers_in(text) - counts


def _to_decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", "").replace("\u00b7", ".")).normalize()
    except InvalidOperation:
        return None
