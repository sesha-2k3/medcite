"""Rerank fused hits with the cross-encoder, which reads question and passage together."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.db.queries.chunks import ChunkView
from app.models.cross_encoder import RelevanceScorer
from app.retrieval.hybrid_search import HybridHit


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: ChunkView
    hit: HybridHit
    relevance: float


def passage_text(chunk: ChunkView) -> str:
    """The title gives the cross-encoder context a lone abstract section lacks."""
    return f"{chunk.title} {chunk.content}"


def rerank(
    question: str, candidates: Sequence[tuple[ChunkView, HybridHit]], scorer: RelevanceScorer
) -> list[ScoredChunk]:
    """Most relevant first; ties keep fusion order."""
    if not candidates:
        return []
    scores = scorer.score(question, [passage_text(chunk) for chunk, _ in candidates])
    scored = [
        ScoredChunk(chunk=chunk, hit=hit, relevance=score)
        for (chunk, hit), score in zip(candidates, scores, strict=True)
    ]
    return sorted(scored, key=lambda s: -s.relevance)
