"""Read models for retrieved chunks: each chunk joined with the article fields ranking needs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, Chunk
from app.ingest.metadata import StudyMetadata


@dataclass(frozen=True, slots=True)
class ChunkView:
    chunk_id: int
    article_id: int
    pmid: int
    section: str | None
    chunk_index: int | None
    content: str
    title: str
    journal: str | None
    pub_date: date | None
    pub_types: tuple[str, ...]
    publication_status: str | None
    study_meta: StudyMetadata


class ChunkRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def load(self, chunk_ids: Sequence[int]) -> dict[int, ChunkView]:
        if not chunk_ids:
            return {}
        query = (
            select(
                Chunk.id,
                Chunk.article_id,
                Article.pmid,
                Chunk.section,
                Chunk.chunk_index,
                Chunk.content,
                Article.title,
                Article.journal,
                Article.pub_date,
                Article.pub_types,
                Article.publication_status,
                Article.study_meta,
            )
            .join(Article, Article.id == Chunk.article_id)
            .where(Chunk.id.in_(chunk_ids))
        )
        async with self._sessions() as session:
            rows = await session.execute(query)
            return {
                row.id: ChunkView(
                    chunk_id=row.id,
                    article_id=row.article_id,
                    pmid=row.pmid or 0,
                    section=row.section,
                    chunk_index=row.chunk_index,
                    content=row.content,
                    title=row.title,
                    journal=row.journal,
                    pub_date=row.pub_date,
                    pub_types=tuple(row.pub_types or ()),
                    publication_status=row.publication_status,
                    study_meta=StudyMetadata.model_validate(row.study_meta or {}),
                )
                for row in rows
            }
