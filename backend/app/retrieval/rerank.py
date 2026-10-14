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
    question: str,
    candidates: Sequence[tuple[ChunkView, HybridHit]],
    scorer: RelevanceScorer,
    cache: dict[int, float] | None = None,
) -> list[ScoredChunk]:
    """Most relevant first; ties keep fusion order.

    ``cache`` maps chunk IDs to scores for this ``question``: tier escalation reranks the
    growing pool several times, and only chunks not scored before go to the model.
    """
    if not candidates:
        return []
    known = cache if cache is not None else {}
    missing = [chunk for chunk, _ in candidates if chunk.chunk_id not in known]
    if missing:
        fresh = scorer.score(question, [passage_text(chunk) for chunk in missing])
        known.update({chunk.chunk_id: score for chunk, score in zip(missing, fresh, strict=True)})
    scored = [
        ScoredChunk(chunk=chunk, hit=hit, relevance=known[chunk.chunk_id])
        for chunk, hit in candidates
    ]
    return sorted(scored, key=lambda s: -s.relevance)
