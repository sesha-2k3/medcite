"""An article with its passages, for the source panel and article page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, Chunk
from app.ingest.chunker import TITLE_ONLY_SECTION
from app.ingest.metadata import StudyMetadata


@dataclass(frozen=True, slots=True)
class Passage:
    chunk_id: int
    section: str | None
    chunk_index: int | None
    content: str


@dataclass(frozen=True, slots=True)
class ArticleDetail:
    pmid: int
    title: str
    journal: str | None
    pub_date: date | None
    doi: str | None
    pub_types: tuple[str, ...]
    publication_status: str | None
    is_retracted: bool
    study_meta: StudyMetadata
    passages: tuple[Passage, ...]


class ArticleRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, pmid: int) -> ArticleDetail | None:
        """Title-only chunks are not passages; a retracted article has none left."""
        async with self._sessions() as session:
            article = (
                await session.execute(select(Article).where(Article.pmid == pmid))
            ).scalar_one_or_none()
            if article is None:
                return None
            chunks = (
                await session.execute(
                    select(Chunk.id, Chunk.section, Chunk.chunk_index, Chunk.content)
                    .where(
                        Chunk.article_id == article.id,
                        Chunk.section.is_distinct_from(TITLE_ONLY_SECTION),
                    )
                    .order_by(Chunk.chunk_index, Chunk.id)
                )
            ).all()
        return ArticleDetail(
            pmid=pmid,
            title=article.title,
            journal=article.journal,
            pub_date=article.pub_date,
            doi=article.doi,
            pub_types=tuple(article.pub_types or ()),
            publication_status=article.publication_status,
            is_retracted=article.is_retracted,
            study_meta=StudyMetadata.model_validate(article.study_meta or {}),
            passages=tuple(
                Passage(
                    chunk_id=c.id, section=c.section, chunk_index=c.chunk_index, content=c.content
                )
                for c in chunks
            ),
        )
