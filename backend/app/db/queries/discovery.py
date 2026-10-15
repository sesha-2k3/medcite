"""Articles for a digest: one citable passage and one embedding per article."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Article, Chunk
from app.db.queries.chunks import ChunkRepository, ChunkView
from app.ingest.chunker import TITLE_ONLY_SECTION


@dataclass(frozen=True, slots=True)
class DiscoveryArticle:
    passage: ChunkView  # the article's conclusions, else results, else its abstract
    vector: npt.NDArray[np.float32]  # mean of its abstract chunk embeddings, unit length


def passage_rank(section: str | None) -> int:
    """Lower is a better passage to cite for an article's main finding."""
    label = (section or "").upper()
    if "CONCLUSION" in label:
        return 0
    if "RESULT" in label or "FINDING" in label:
        return 1
    if label == "ABSTRACT":
        return 2
    return 3


class DiscoveryArticles:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._chunks = ChunkRepository(sessions)

    async def load(self, pmids: Sequence[int]) -> list[DiscoveryArticle]:
        """In ``pmids`` order. Articles without abstract text (title-only, retracted or not
        stored) are left out: a digest can only cite passages.
        """
        if not pmids:
            return []
        query = (
            select(Article.pmid, Chunk.id, Chunk.section, Chunk.embedding)
            .join(Article, Article.id == Chunk.article_id)
            .where(
                Article.pmid.in_(list(pmids)),
                Article.is_retracted.is_(False),
                Chunk.section.is_distinct_from(TITLE_ONLY_SECTION),
                Chunk.embedding.is_not(None),
            )
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()

        by_pmid: dict[int, list[tuple[int, str | None, npt.NDArray[np.float32]]]] = {}
        for row in rows:
            by_pmid.setdefault(row.pmid, []).append((row.id, row.section, row.embedding))
        chosen = {
            pmid: min(chunks, key=lambda c: (passage_rank(c[1]), c[0]))[0]
            for pmid, chunks in by_pmid.items()
        }
        views = await self._chunks.load(list(chosen.values()))
        articles: list[DiscoveryArticle] = []
        for pmid in dict.fromkeys(pmids):
            if pmid not in chosen:
                continue
            mean = np.mean(np.stack([np.asarray(c[2]) for c in by_pmid[pmid]]), axis=0)
            norm = float(np.linalg.norm(mean)) or 1.0
            articles.append(
                DiscoveryArticle(
                    passage=views[chosen[pmid]], vector=(mean / norm).astype(np.float32)
                )
            )
        return articles
