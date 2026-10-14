"""Write articles and their chunks to Postgres.

Each article is one transaction: upsert the article row by PMID, then replace all of its chunks,
so re-ingesting an article always leaves exactly one current set of chunks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, delete, func, insert, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import ReturningInsert

from app.db.models import Article, Chunk
from app.ingest.chunker import ChunkDraft
from app.ingest.metadata import StudyMetadata
from app.ingest.pubmed_parser import ParsedArticle
from app.models.embeddings import Vectors

# Columns refreshed on re-ingestion. has_full_text, license_code and evidence_level belong to
# later stages and are never overwritten here.
REFRESHED_COLUMNS = (
    "pmcid",
    "doi",
    "title",
    "journal",
    "pub_date",
    "entrez_date",
    "pub_types",
    "mesh_terms",
    "is_retracted",
    "study_meta",
    "last_checked_at",
)


class StoreOutcome(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    RETRACTED = "retracted"
    SKIPPED_RETRACTED = "skipped_retracted"


@dataclass(frozen=True, slots=True)
class ArticleRecord:
    article: ParsedArticle
    metadata: StudyMetadata | None
    chunks: Sequence[ChunkDraft]
    embeddings: Vectors | None
    embed_model: str


class ArticleStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def existing_pmids(self, pmids: Sequence[int]) -> set[int]:
        if not pmids:
            return set()
        async with self._sessions() as session:
            rows = await session.scalars(select(Article.pmid).where(Article.pmid.in_(pmids)))
            return {pmid for pmid in rows if pmid is not None}

    async def save(self, record: ArticleRecord) -> StoreOutcome:
        async with self._sessions.begin() as session:
            if record.article.is_retracted:
                return await _retire(session, record.article.pmid)
            article_id, inserted = await _upsert_article(session, record)
            await session.execute(delete(Chunk).where(Chunk.article_id == article_id))
            if record.chunks:
                await session.execute(insert(Chunk), _chunk_rows(article_id, record))
        return StoreOutcome.INSERTED if inserted else StoreOutcome.UPDATED


async def _retire(session: AsyncSession, pmid: int) -> StoreOutcome:
    """Never store a retracted article; if we already hold it, flag it and drop its chunks."""
    article_id = await session.scalar(
        update(Article)
        .where(Article.pmid == pmid)
        .values(is_retracted=True, last_checked_at=func.now())
        .returning(Article.id)
    )
    if article_id is None:
        return StoreOutcome.SKIPPED_RETRACTED
    await session.execute(delete(Chunk).where(Chunk.article_id == article_id))
    return StoreOutcome.RETRACTED


async def _upsert_article(session: AsyncSession, record: ArticleRecord) -> tuple[int, bool]:
    insert_row = pg_insert(Article).values(**_article_row(record))
    # xmax is 0 only for a freshly inserted row, so it tells an insert from an update.
    was_inserted = literal_column("(xmax = 0)", Boolean).label("inserted")
    upsert: ReturningInsert[int, bool] = insert_row.on_conflict_do_update(
        index_elements=[Article.pmid],
        set_={column: insert_row.excluded[column] for column in REFRESHED_COLUMNS},
    ).returning(Article.id, was_inserted)
    row = (await session.execute(upsert)).one()
    return int(row.id), bool(row.inserted)


def _article_row(record: ArticleRecord) -> dict[str, Any]:
    article = record.article
    return {
        "pmid": article.pmid,
        "pmcid": article.pmcid,
        "doi": article.doi,
        "title": article.title,
        "journal": article.journal,
        "pub_date": article.pub_date,
        "entrez_date": article.entrez_date,
        "pub_types": list(article.pub_types),
        "mesh_terms": list(article.mesh_terms),
        "is_retracted": False,
        "study_meta": record.metadata.model_dump(mode="json") if record.metadata else None,
        "last_checked_at": func.now(),
    }


def _chunk_rows(article_id: int, record: ArticleRecord) -> list[dict[str, Any]]:
    if record.embeddings is not None and len(record.embeddings) != len(record.chunks):
        raise ValueError("one embedding is required per chunk")
    return [
        {
            "article_id": article_id,
            "section": chunk.section,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "embedding": record.embeddings[i] if record.embeddings is not None else None,
            "embed_model": record.embed_model,
        }
        for i, chunk in enumerate(record.chunks)
    ]
