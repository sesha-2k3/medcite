"""Small builders for retrieval tests."""

from __future__ import annotations

from app.db.queries.chunks import ChunkView
from app.ingest.metadata import StudyMetadata
from app.retrieval.hybrid_search import HybridHit
from app.retrieval.rerank import ScoredChunk


def view(
    chunk_id: int,
    article_id: int,
    *,
    section: str = "Abstract",
    content: str = "text",
    meta: StudyMetadata | None = None,
) -> ChunkView:
    return ChunkView(
        chunk_id=chunk_id,
        article_id=article_id,
        pmid=90_000_000 + article_id,
        section=section,
        chunk_index=0,
        content=content,
        title=f"Article {article_id}",
        journal=None,
        pub_date=None,
        pub_types=(),
        publication_status=None,
        study_meta=meta or StudyMetadata(),
    )


def scored(
    chunk_id: int, article_id: int, relevance: float, *, section: str = "Abstract"
) -> ScoredChunk:
    return ScoredChunk(
        chunk=view(chunk_id, article_id, section=section),
        hit=HybridHit(chunk_id=chunk_id, rrf_score=0.01, dense_rank=1, sparse_rank=None),
        relevance=relevance,
    )
