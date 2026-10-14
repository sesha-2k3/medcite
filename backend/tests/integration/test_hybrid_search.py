from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.queries.chunks import ChunkRepository
from app.retrieval.hybrid_search import HybridSearcher, keyword_query
from tests.retrieval.seed import seed, unit_vector

pytestmark = pytest.mark.integration

TIRZEPATIDE, METFORMIN, TRAINING, TITLE_ONLY = 90001001, 90001002, 90001003, 90001004
VECTORS = {pmid: unit_vector(pmid) for pmid in (TIRZEPATIDE, METFORMIN, TRAINING, TITLE_ONLY)}


@pytest.fixture
async def corpus(sessions: async_sessionmaker[AsyncSession]) -> async_sessionmaker[AsyncSession]:
    await seed(
        sessions,
        TIRZEPATIDE,
        "Incretin therapy and body composition",
        [
            (
                "Abstract",
                "Tirzepatide reduced lean mass in adults with obesity.",
                VECTORS[TIRZEPATIDE],
            )
        ],
    )
    await seed(
        sessions,
        METFORMIN,
        "Glucose lowering in older adults",
        [("Abstract", "Metformin slowed kidney function decline.", VECTORS[METFORMIN])],
    )
    await seed(
        sessions,
        TRAINING,
        "Curl training and elbow flexors",
        [("Abstract", "Dumbbell curls increased biceps thickness.", VECTORS[TRAINING])],
    )
    await seed(
        sessions,
        TITLE_ONLY,
        "Tirzepatide commentary",
        [("Title", "Tirzepatide commentary", VECTORS[TITLE_ONLY])],
    )
    return sessions


async def pmids_of(sessions: async_sessionmaker[AsyncSession], chunk_ids: list[int]) -> list[int]:
    views = await ChunkRepository(sessions).load(chunk_ids)
    return [views[chunk_id].pmid for chunk_id in chunk_ids]


async def test_exact_drug_name_wins_through_the_sparse_leg_despite_weak_dense_match(
    corpus: async_sessionmaker[AsyncSession],
) -> None:
    """The acceptance test: the query vector points away from the tirzepatide chunk."""
    searcher = HybridSearcher(corpus)
    hits = await searcher.search(-VECTORS[TIRZEPATIDE], keyword_query("tirzepatide"))

    assert (await pmids_of(corpus, [hits[0].chunk_id])) == [TIRZEPATIDE]
    assert hits[0].sparse_rank == 1
    assert hits[0].dense_rank == 3


async def test_natural_language_question_still_matches_keywords(
    corpus: async_sessionmaker[AsyncSession],
) -> None:
    question = "Does tirzepatide change lean mass in people with obesity?"
    hits = await HybridSearcher(corpus).search(unit_vector(1), keyword_query(question))
    assert (await pmids_of(corpus, [hits[0].chunk_id])) == [TIRZEPATIDE]


async def test_chunk_found_by_both_legs_outranks_single_leg_matches(
    corpus: async_sessionmaker[AsyncSession],
) -> None:
    hits = await HybridSearcher(corpus).search(VECTORS[METFORMIN], keyword_query("metformin"))
    top = hits[0]
    assert (await pmids_of(corpus, [top.chunk_id])) == [METFORMIN]
    assert (top.dense_rank, top.sparse_rank) == (1, 1)
    assert top.rrf_score == pytest.approx(2 / 61)
    assert all(h.rrf_score < top.rrf_score for h in hits[1:])


async def test_title_only_chunks_are_never_returned(
    corpus: async_sessionmaker[AsyncSession],
) -> None:
    hits = await HybridSearcher(corpus).search(VECTORS[TITLE_ONLY], keyword_query("tirzepatide"))
    assert TITLE_ONLY not in await pmids_of(corpus, [h.chunk_id for h in hits])
    assert len(hits) == 3


async def test_candidate_restriction(corpus: async_sessionmaker[AsyncSession]) -> None:
    searcher = HybridSearcher(corpus)
    hits = await searcher.search(
        VECTORS[TIRZEPATIDE], keyword_query("tirzepatide"), pmids=[METFORMIN, TRAINING]
    )
    assert sorted(await pmids_of(corpus, [h.chunk_id for h in hits])) == [METFORMIN, TRAINING]
    assert await searcher.search(VECTORS[METFORMIN], "metformin", pmids=[]) == []


async def test_limit_and_per_leg(corpus: async_sessionmaker[AsyncSession]) -> None:
    hits = await HybridSearcher(corpus).search(
        VECTORS[TRAINING], keyword_query("curl"), per_leg=1, limit=1
    )
    assert len(hits) == 1
