"""Choose the final context: the most relevant chunks, without letting one article dominate."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.ingest.chunker import ABSTRACT_SECTION
from app.retrieval.rerank import ScoredChunk


@dataclass(frozen=True, slots=True)
class DiversityLimits:
    min_keep: int = 8
    max_keep: int = 15
    per_article: int = 3
    relevance_floor: float | None = None


def select_diverse(
    ranked: Sequence[ScoredChunk], limits: DiversityLimits | None = None
) -> list[ScoredChunk]:
    """Walk chunks by relevance, keeping at most ``per_article`` per article and ``max_keep``
    in total. Below ``relevance_floor``, stop once ``min_keep`` are kept. Every kept article
    then gets an abstract chunk if the pool has one, so each cited study has its summary.
    """
    limits = limits or DiversityLimits()
    kept: list[ScoredChunk] = []
    per_article: Counter[int] = Counter()
    for item in ranked:
        if len(kept) >= limits.max_keep:
            break
        below_floor = limits.relevance_floor is not None and item.relevance < limits.relevance_floor
        if below_floor and len(kept) >= limits.min_keep:
            break
        if per_article[item.chunk.article_id] < limits.per_article:
            kept.append(item)
            per_article[item.chunk.article_id] += 1
    return _ensure_abstracts(kept, ranked, limits.per_article)


def is_abstract(item: ScoredChunk) -> bool:
    return (item.chunk.section or "").startswith(ABSTRACT_SECTION)


def _ensure_abstracts(
    kept: list[ScoredChunk], pool: Sequence[ScoredChunk], per_article: int
) -> list[ScoredChunk]:
    result = list(kept)
    for article_id in dict.fromkeys(item.chunk.article_id for item in kept):
        mine = [item for item in result if item.chunk.article_id == article_id]
        if any(is_abstract(item) for item in mine):
            continue
        abstract = next(
            (p for p in pool if p.chunk.article_id == article_id and is_abstract(p)), None
        )
        if abstract is None:
            continue
        if len(mine) >= per_article:
            result.remove(min(mine, key=lambda item: item.relevance))
        result.append(abstract)
    return sorted(result, key=lambda item: -item.relevance)
