"""Wiring for the retriever."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.queries.chunks import ChunkRepository
from app.models.cross_encoder import RelevanceScorer
from app.models.embeddings import Embedder
from app.retrieval.diversity import DiversityLimits
from app.retrieval.evidence import EvidenceThresholds
from app.retrieval.hybrid_search import HybridSearcher
from app.retrieval.retriever import RetrievalConfig, Retriever


def retrieval_config(settings: Settings) -> RetrievalConfig:
    return RetrievalConfig(
        per_leg=settings.retrieval_per_leg,
        rerank_top_n=settings.rerank_top_n,
        limits=DiversityLimits(
            min_keep=settings.context_min_chunks,
            max_keep=settings.context_max_chunks,
            per_article=settings.max_chunks_per_article,
            relevance_floor=settings.rerank_relevance_floor,
        ),
        thresholds=EvidenceThresholds(
            small_sample=settings.small_sample_threshold,
            short_follow_up_weeks=settings.short_follow_up_weeks,
        ),
    )


def build_retriever(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    scorer: RelevanceScorer,
) -> Retriever:
    return Retriever(
        searcher=HybridSearcher(sessions, rrf_k=settings.rrf_k),
        chunks=ChunkRepository(sessions),
        embedder=embedder,
        scorer=scorer,
        config=retrieval_config(settings),
    )
