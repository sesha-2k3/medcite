"""Question in, graded evidence passages out.

embed question → hybrid search (candidates, then whole index) → merge → rerank
→ diversity caps → attach evidence grades
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from app.db.queries.chunks import ChunkRepository
from app.ingest.metadata import OutcomeClass
from app.models.cross_encoder import RelevanceScorer
from app.models.embeddings import Embedder
from app.retrieval.diversity import DiversityLimits, select_diverse
from app.retrieval.evidence import EvidenceGrade, EvidenceThresholds, grade_study
from app.retrieval.hybrid_search import HybridHit, HybridSearcher, keyword_query, merge_hits
from app.retrieval.rerank import ScoredChunk, rerank


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    per_leg: int = 100
    rerank_top_n: int = 60
    limits: DiversityLimits = field(default_factory=DiversityLimits)
    thresholds: EvidenceThresholds = field(default_factory=EvidenceThresholds)


@dataclass(frozen=True, slots=True)
class EvidencePassage:
    scored: ScoredChunk
    grade: EvidenceGrade
    tier: int | None


class Retriever:
    def __init__(
        self,
        searcher: HybridSearcher,
        chunks: ChunkRepository,
        embedder: Embedder,
        scorer: RelevanceScorer,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._searcher = searcher
        self._chunks = chunks
        self._embedder = embedder
        self._scorer = scorer
        self._config = config or RetrievalConfig()

    async def retrieve(
        self,
        question: str,
        *,
        candidate_tiers: Mapping[int, int] | None = None,
        question_outcome: OutcomeClass | None = None,
    ) -> list[EvidencePassage]:
        """``candidate_tiers`` maps PMIDs found for this question to the tier that found them;
        a second pass over the whole local index can add articles found by earlier questions.
        """
        config = self._config
        [query_vec] = await asyncio.to_thread(self._embedder.embed_queries, [question])
        keywords = keyword_query(question)
        per_leg, limit = config.per_leg, config.rerank_top_n
        passes: list[list[HybridHit]] = []
        if candidate_tiers:
            restricted = await self._searcher.search(
                query_vec, keywords, pmids=list(candidate_tiers), per_leg=per_leg, limit=limit
            )
            passes.append(restricted)
        passes.append(
            await self._searcher.search(query_vec, keywords, per_leg=per_leg, limit=limit)
        )
        hits = merge_hits(*passes)[: config.rerank_top_n]
        views = await self._chunks.load([hit.chunk_id for hit in hits])
        pairs = [(views[hit.chunk_id], hit) for hit in hits if hit.chunk_id in views]
        ranked = await asyncio.to_thread(rerank, question, pairs, self._scorer)
        kept = select_diverse(ranked, config.limits)

        tiers = candidate_tiers or {}
        return [
            EvidencePassage(
                scored=item,
                grade=grade_study(
                    item.chunk.study_meta,
                    publication_status=item.chunk.publication_status,
                    question_outcome=question_outcome,
                    thresholds=config.thresholds,
                ),
                tier=tiers.get(item.chunk.pmid),
            )
            for item in kept
        ]
