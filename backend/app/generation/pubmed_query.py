"""Build and check PubMed query strings.

Queries are assembled in code from concepts rather than written by the LLM, so every concept
pairs its MeSH headings with title/abstract terms (new records may not be MeSH-indexed yet),
quoting is always balanced, and retracted publications are always excluded.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

RETRACTED_EXCLUSION = 'NOT "retracted publication"[pt]'
MAX_MESH_TERMS = 6
MAX_TEXT_TERMS = 8
MAX_TERM_LENGTH = 80

KNOWN_TAGS = frozenset(
    {"mh", "majr", "tiab", "ti", "ab", "tw", "pt", "dp", "edat", "la", "sb", "filter"}
)
_TAG = re.compile(r"\[([^\]]*)\]")
_OPERATOR = r"(?:AND|OR|NOT)"
_DANGLING = (
    (re.compile(rf"^\s*{_OPERATOR}\b"), "starts with an operator"),
    (re.compile(rf"\b{_OPERATOR}\s*$"), "ends with an operator"),
    (re.compile(rf"\b{_OPERATOR}\s+{_OPERATOR}\b"), "two operators in a row"),
    (re.compile(r"\(\s*\)"), "empty parentheses"),
    (re.compile(rf"\(\s*{_OPERATOR}\b"), "operator after '('"),
    (re.compile(rf"\b{_OPERATOR}\s*\)"), "operator before ')'"),
)
# MeSH check tags and demographic headings: precise as [mh], but as free text ("aged 20 to
# 40 years", "adult patients") they match almost anything, so they are never echoed as text.
CHECK_TAGS = frozenset(
    heading.lower()
    for heading in (
        "Humans",
        "Animals",
        "Male",
        "Female",
        "Infant",
        "Child",
        "Child, Preschool",
        "Adolescent",
        "Young Adult",
        "Adult",
        "Middle Aged",
        "Aged",
        "Aged, 80 and over",
    )
)
_UNSAFE = re.compile(r'["()\[\]]')
_BOOLEAN_WORD = re.compile(rf"\b{_OPERATOR}\b")


@dataclass(frozen=True, slots=True)
class Terms:
    """One concept: MeSH headings plus free-text synonyms (``*`` truncation allowed)."""

    mesh: Sequence[str] = ()
    text: Sequence[str] = ()


def clean_term(term: str) -> str | None:
    """Strip characters that would break the query; drop terms that can't be made safe."""
    cleaned = " ".join(_UNSAFE.sub(" ", term).split())
    if not cleaned or len(cleaned) > MAX_TERM_LENGTH or _BOOLEAN_WORD.search(cleaned):
        return None
    return cleaned


def concept_clause(terms: Terms) -> str | None:
    """``("Resistance Training"[mh] OR "strength train*"[tiab] OR ...)``, or None if empty."""
    mesh = _unique(clean_term(t) for t in terms.mesh)[:MAX_MESH_TERMS]
    text = _unique(clean_term(t) for t in terms.text)[:MAX_TEXT_TERMS]
    # Every heading doubles as a text term, so records not yet MeSH-indexed still match.
    echoed = (clean_term(natural_order(m)) for m in mesh if m.lower() not in CHECK_TAGS)
    text = _unique([*text, *echoed])
    parts = [f'"{m}"[mh]' for m in mesh] + [f'"{t}"[tiab]' for t in text]
    return f"({' OR '.join(parts)})" if parts else None


def natural_order(heading: str) -> str:
    """MeSH inverts some headings: 'Diabetes Mellitus, Type 2' reads 'type 2 diabetes mellitus'."""
    return " ".join(part.strip() for part in reversed(heading.split(","))).lower()


def any_of(filters: Iterable[str]) -> str:
    return f"({' OR '.join(filters)})"


def date_filter(start: date, end: date | None, field: str) -> str:
    """Inclusive window, open-ended when ``end`` is None (PubMed's '3000' convention)."""
    upper = end.strftime("%Y/%m/%d") if end else "3000"
    return f"{start.strftime('%Y/%m/%d')}:{upper}[{field}]"


def compose(
    clauses: Sequence[str],
    *,
    include: str | None = None,
    exclude: str | None = None,
    dates: str | None = None,
) -> str:
    """AND the concept clauses with optional filters; always exclude retractions."""
    query = " AND ".join(clauses)
    if include:
        query = f"({query}) AND {include}"
    if dates:
        query = f"({query}) AND {dates}"
    if exclude:
        query = f"({query}) NOT {exclude}"
    return f"({query}) {RETRACTED_EXCLUSION}"


def validate_query(query: str) -> list[str]:
    """Problems that would make PubMed misread the query; empty when it is well formed."""
    errors: list[str] = []
    depth, in_quotes = 0, False
    for char in query:
        if char == '"':
            in_quotes = not in_quotes
        elif not in_quotes and char == "(":
            depth += 1
        elif not in_quotes and char == ")":
            depth -= 1
            if depth < 0:
                errors.append("')' without matching '('")
                depth = 0
    if in_quotes:
        errors.append("unbalanced quotes")
    if depth:
        errors.append("unbalanced parentheses")
    for tag in _TAG.findall(query):
        if tag.strip().lower() not in KNOWN_TAGS:
            errors.append(f"unknown field tag [{tag}]")
    outside_quotes = re.sub(r'"[^"]*"', '""', query)
    errors.extend(message for pattern, message in _DANGLING if pattern.search(outside_quotes))
    if not query.strip():
        errors.append("empty query")
    return errors


def _unique(values: Iterable[str | None]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        if value and value.lower() not in seen:
            seen[value.lower()] = value
    return list(seen.values())
