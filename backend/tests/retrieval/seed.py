"""Store synthetic articles with chosen chunk texts and embeddings."""

from __future__ import annotations

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import EMBEDDING_DIM
from app.ingest.chunker import ChunkDraft
from app.ingest.metadata import StudyMetadata
from app.ingest.pubmed_parser import ParsedArticle
from app.ingest.store import ArticleRecord, ArticleStore
from app.models.embeddings import Vectors


def unit_vector(seed: int) -> Vectors:
    vec = np.random.default_rng(seed).standard_normal(EMBEDDING_DIM).astype(np.float32)
    normalized: Vectors = vec / np.linalg.norm(vec)
    return normalized


def article(pmid: int, title: str) -> ParsedArticle:
    return ParsedArticle(
        pmid=pmid,
        title=title,
        title_translated=False,
        vernacular_title=None,
        journal=None,
        language="eng",
        pub_date=None,
        entrez_date=None,
        doi=None,
        pmcid=None,
        pub_types=("Journal Article",),
        mesh_terms=(),
        abstract=(),
        is_retracted=False,
    )


async def seed(
    sessions: async_sessionmaker[AsyncSession],
    pmid: int,
    title: str,
    chunks: list[tuple[str, str, Vectors]],
    meta: StudyMetadata | None = None,
) -> None:
    """``chunks`` holds (section, content, embedding) triples."""
    drafts = [
        ChunkDraft(section, index, content, title, content)
        for index, (section, content, _) in enumerate(chunks)
    ]
    vectors = np.vstack([vec for _, _, vec in chunks]).astype(np.float32)
    record = ArticleRecord(article(pmid, title), meta or StudyMetadata(), drafts, vectors, "test")
    await ArticleStore(sessions).save(record)
