from __future__ import annotations

import dataclasses

import numpy as np
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.db.models import Article, Chunk
from app.db.session import create_session_factory
from app.ingest.chunker import chunk_article
from app.ingest.metadata import fallback_metadata
from app.ingest.pubmed_parser import ParsedArticle
from app.ingest.service import IngestService
from app.ingest.store import ArticleRecord, ArticleStore, StoreOutcome
from tests.ingest.fakes import FixtureFetcher, HashEmbedder, PubTypeMetadata
from tests.ingest.helpers import EDGE_CASES, edge_cases

pytestmark = pytest.mark.integration

SYNTHETIC_PMIDS = 90_000_000


@pytest.fixture
async def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    factory = create_session_factory(engine)
    async with factory.begin() as session:
        await session.execute(delete(Article).where(Article.pmid >= SYNTHETIC_PMIDS))
    return factory


def record(article: ParsedArticle, embedder: HashEmbedder | None = None) -> ArticleRecord:
    embedder = embedder or HashEmbedder()
    metadata = fallback_metadata(article)
    chunks = chunk_article(article, metadata.design)
    vectors = embedder.embed_articles([(c.embed_title, c.embed_text) for c in chunks])
    return ArticleRecord(article, metadata, chunks, vectors, embedder.article_model)


async def chunk_rows(
    sessions: async_sessionmaker[AsyncSession], pmid: int
) -> list[tuple[int, str, int]]:
    async with sessions() as session:
        rows = await session.execute(
            select(Chunk.id, Chunk.section, Chunk.chunk_index)
            .join(Article)
            .where(Article.pmid == pmid)
            .order_by(Chunk.chunk_index)
        )
        return [tuple(row) for row in rows]  # type: ignore[misc]


async def test_saving_twice_leaves_exactly_one_set_of_chunks(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = ArticleStore(sessions)
    rct = edge_cases()[90000101]

    assert await store.save(record(rct)) is StoreOutcome.INSERTED
    first = await chunk_rows(sessions, rct.pmid)
    assert await store.save(record(rct)) is StoreOutcome.UPDATED
    second = await chunk_rows(sessions, rct.pmid)

    assert [(s, i) for _, s, i in second] == [(s, i) for _, s, i in first]
    assert len(second) == 4
    assert {cid for cid, _, _ in first}.isdisjoint(cid for cid, _, _ in second)
    async with sessions() as session:
        count = select(func.count()).select_from(Article).where(Article.pmid == rct.pmid)
        assert await session.scalar(count) == 1


async def test_article_fields_metadata_and_embeddings_are_stored(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    rct = edge_cases()[90000101]
    saved = record(rct)
    await ArticleStore(sessions).save(saved)

    async with sessions() as session:
        article = await session.scalar(select(Article).where(Article.pmid == rct.pmid))
        chunk = await session.scalar(
            select(Chunk)
            .join(Article)
            .where(Article.pmid == rct.pmid, Chunk.section == "Abstract-RESULTS")
        )
    assert article is not None
    assert article.doi == "10.1000/synthetic.101"
    assert article.pub_types == ["Journal Article", "Randomized Controlled Trial"]
    assert article.study_meta is not None
    assert article.study_meta["design"] == "rct"
    assert article.last_checked_at is not None
    assert chunk is not None
    assert chunk.embed_model == "fake/hash-embedder"
    assert chunk.embedding is not None
    assert saved.embeddings is not None
    np.testing.assert_allclose(chunk.embedding, saved.embeddings[2], rtol=1e-6)


async def test_new_retracted_article_is_not_stored(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    retracted = edge_cases()[90000104]
    outcome = await ArticleStore(sessions).save(ArticleRecord(retracted, None, [], None, "m"))
    assert outcome is StoreOutcome.SKIPPED_RETRACTED
    assert await ArticleStore(sessions).existing_pmids([retracted.pmid]) == set()


async def test_stored_article_that_becomes_retracted_loses_its_chunks(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = ArticleStore(sessions)
    rct = edge_cases()[90000101]
    await store.save(record(rct))

    retracted_now = dataclasses.replace(rct, is_retracted=True)
    assert await store.save(ArticleRecord(retracted_now, None, [], None, "m")) is (
        StoreOutcome.RETRACTED
    )
    assert await chunk_rows(sessions, rct.pmid) == []
    async with sessions() as session:
        flag = await session.scalar(select(Article.is_retracted).where(Article.pmid == rct.pmid))
    assert flag is True


async def test_reingestion_never_overwrites_later_stage_columns(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = ArticleStore(sessions)
    rct = edge_cases()[90000101]
    await store.save(record(rct))
    async with sessions.begin() as session:
        await session.execute(
            update(Article)
            .where(Article.pmid == rct.pmid)
            .values(has_full_text=True, evidence_level="2")
        )
    await store.save(record(rct))
    async with sessions() as session:
        row = (
            await session.execute(
                select(Article.has_full_text, Article.evidence_level).where(
                    Article.pmid == rct.pmid
                )
            )
        ).one()
    assert tuple(row) == (True, "2")


async def test_failed_save_rolls_back_completely(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    store = ArticleStore(sessions)
    rct = edge_cases()[90000101]
    await store.save(record(rct))
    before = await chunk_rows(sessions, rct.pmid)

    good = record(rct)
    assert good.embeddings is not None
    broken = ArticleRecord(rct, good.metadata, good.chunks, good.embeddings[:2], "m")
    with pytest.raises(ValueError, match="one embedding"):
        await store.save(broken)
    assert await chunk_rows(sessions, rct.pmid) == before


async def test_service_ingests_new_pmids_and_skips_stored_ones(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    fetcher, embedder, metadata = FixtureFetcher(EDGE_CASES), HashEmbedder(), PubTypeMetadata()
    service = IngestService(fetcher, metadata, embedder, ArticleStore(sessions))
    pmids = [*range(90000101, 90000108), 90000999]

    first = await service.ingest(pmids)
    assert first.outcomes == {StoreOutcome.INSERTED: 6, StoreOutcome.SKIPPED_RETRACTED: 1}
    assert first.not_returned == [90000999]
    assert 90000104 not in metadata.seen
    assert embedder.calls == [10]

    second = await service.ingest(pmids)
    assert second.already_stored == 6
    assert fetcher.requests[-1] == [90000104, 90000999]
    assert second.outcomes == {StoreOutcome.SKIPPED_RETRACTED: 1}

    refreshed = await service.ingest(pmids, refresh=True)
    assert refreshed.outcomes[StoreOutcome.UPDATED] == 6


async def test_title_only_chunks_exist_for_articles_without_abstracts(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    service = IngestService(
        FixtureFetcher(EDGE_CASES), PubTypeMetadata(), HashEmbedder(), ArticleStore(sessions)
    )
    await service.ingest([90000103])
    assert [s for _, s, _ in await chunk_rows(sessions, 90000103)] == ["Title"]
