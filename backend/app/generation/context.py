"""Turn ranked passages into the numbered context the generator and judge read.

Passages are grouped by article so each article's header (design, n, year, directness,
outcome, flags) and title appear once; articles follow their best passage's rank, and chunks
within an article follow abstract order. Markers ``[C1]...[Cn]`` are assigned in that final
order. The context must fit a token budget (Groq's free plan allows about 8K tokens a minute
per model): passages are trimmed at sentence boundaries, and the lowest-ranked ones are dropped
first, down to ``min_chunks``, before trimming harder.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.db.queries.chunks import ChunkView
from app.ingest.chunker import ABSTRACT_SECTION, TITLE_ONLY_SECTION
from app.ingest.metadata import Design
from app.retrieval.evidence import DIRECT_TIERS, CitedEvidence, RatingSuggestion, suggest_rating
from app.retrieval.retriever import EvidencePassage

# Live runs on gpt-oss-120b measured about 4.1 characters per token over prompt and context;
# 4.0 stays slightly conservative. make answer prints estimated and reported tokens.
CHARS_PER_TOKEN = 4.0
MIN_PASSAGE_CHARS = 300
TRIM_STEP = 0.85
ELLIPSIS = "\u2026"

DESIGN_LABELS: dict[Design, str] = {
    "meta_analysis": "meta-analysis",
    "systematic_review": "systematic review",
    "guideline": "guideline",
    "rct": "RCT",
    "cohort": "cohort",
    "case_control": "case-control",
    "cross_sectional": "cross-sectional",
    "case_series": "case series",
    "case_report": "case report",
    "narrative_review": "narrative review",
    "commentary": "commentary",
    "mechanistic": "mechanistic",
    "animal": "animal study",
    "in_vitro": "in vitro",
    "other": "unclassified",
}
_SENTENCE_END = re.compile(r"[.!?](?=\s)")


@dataclass(frozen=True, slots=True)
class ContextBudget:
    max_tokens: int = 3400
    passage_max_chars: int = 1400
    min_chunks: int = 8
    max_chunks: int = 15


@dataclass(frozen=True, slots=True)
class ContextItem:
    marker: str
    passage: EvidencePassage
    text: str  # the passage as the generator saw it (possibly trimmed)

    @property
    def chunk(self) -> ChunkView:
        return self.passage.scored.chunk


@dataclass(frozen=True, slots=True)
class EvidenceContext:
    items: tuple[ContextItem, ...]
    by_marker: Mapping[str, ContextItem]
    text: str
    estimated_tokens: int
    omitted: int  # passages left out to fit the budget


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def build_context(
    passages: Sequence[EvidencePassage], budget: ContextBudget | None = None
) -> EvidenceContext:
    """``passages`` must be in rank order, as the retriever returns them."""
    budget = budget or ContextBudget()
    for passage in passages:
        chunk = passage.scored.chunk
        if chunk.section == TITLE_ONLY_SECTION:
            raise ValueError(f"title-only chunk {chunk.chunk_id} (PMID {chunk.pmid}) in context")

    selected = list(passages[: budget.max_chunks])
    cap = budget.passage_max_chars
    while True:
        items, text = _render(selected, cap)
        tokens = estimate_tokens(text)
        if tokens <= budget.max_tokens or not selected:
            break
        if len(selected) > budget.min_chunks:
            selected.pop()
        elif cap > MIN_PASSAGE_CHARS:
            cap = max(MIN_PASSAGE_CHARS, int(cap * TRIM_STEP))
        elif len(selected) > 1:
            selected.pop()  # the hard budget wins over the minimum
        else:
            break
    return EvidenceContext(
        items=tuple(items),
        by_marker={item.marker: item for item in items},
        text=text,
        estimated_tokens=tokens,
        omitted=len(passages) - len(selected),
    )


def rating_ceiling(items: Sequence[ContextItem]) -> RatingSuggestion:
    """``suggest_rating`` over the distinct articles behind ``items``."""
    by_article = {item.chunk.article_id: item.passage for item in items}
    return suggest_rating([CitedEvidence(p.grade, p.tier) for p in by_article.values()])


def directness(tier: int | None) -> str | None:
    if tier is None:
        return None
    return "direct" if tier in DIRECT_TIERS else "indirect"


def section_label(section: str | None) -> str:
    if not section:
        return "TEXT"
    if section.startswith(f"{ABSTRACT_SECTION}-"):
        return section.removeprefix(f"{ABSTRACT_SECTION}-")
    return section.upper()


def article_header(passage: EvidencePassage) -> str:
    """``## PMID 40082069 | RCT | n=15 | 2025 | indirect | outcome: ... | flags: ...``"""
    chunk, grade = passage.scored.chunk, passage.grade
    meta = chunk.study_meta
    parts = [f"## PMID {chunk.pmid}", DESIGN_LABELS[grade.design]]
    if meta.sample_size is not None:
        parts.append(f"n={meta.sample_size}")
    if chunk.pub_date is not None:
        parts.append(str(chunk.pub_date.year))
    if (reach := directness(passage.tier)) is not None:
        parts.append(reach)
    if meta.primary_outcome or meta.outcome_class != "unknown":
        kind = f" ({meta.outcome_class})" if meta.outcome_class != "unknown" else ""
        parts.append(f"outcome: {meta.primary_outcome or 'not stated'}{kind}")
    if grade.modifiers:
        parts.append("flags: " + ", ".join(m.replace("_", " ") for m in grade.modifiers))
    return " | ".join(parts)


def trim_text(text: str, max_chars: int) -> str:
    """Whitespace-normalized ``text``, cut at the last sentence end within ``max_chars``."""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    cut = ends[-1] if ends and ends[-1] >= max_chars // 2 else window.rfind(" ")
    if cut <= 0:
        cut = max_chars
    return f"{window[:cut].rstrip()} {ELLIPSIS}"


def _render(passages: Sequence[EvidencePassage], cap: int) -> tuple[list[ContextItem], str]:
    groups: dict[int, list[EvidencePassage]] = {}
    for passage in passages:
        groups.setdefault(passage.scored.chunk.article_id, []).append(passage)

    items: list[ContextItem] = []
    lines: list[str] = []
    for group in groups.values():
        ordered = sorted(group, key=lambda p: p.scored.chunk.chunk_index or 0)
        lines.append(article_header(ordered[0]))
        lines.append(f"Title: {ordered[0].scored.chunk.title}")
        for passage in ordered:
            marker = f"C{len(items) + 1}"
            text = trim_text(passage.scored.chunk.content, cap)
            items.append(ContextItem(marker=marker, passage=passage, text=text))
            lines.append(f"[{marker}] {section_label(passage.scored.chunk.section)}: {text}")
        lines.append("")
    return items, "\n".join(lines).strip()
