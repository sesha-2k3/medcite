from __future__ import annotations

from collections import Counter

from app.retrieval.diversity import DiversityLimits, select_diverse
from tests.retrieval.builders import scored


def test_keeps_at_most_max_in_relevance_order() -> None:
    ranked = [scored(i, article_id=i, relevance=100 - i) for i in range(30)]
    kept = select_diverse(ranked, DiversityLimits(max_keep=15))
    assert [k.chunk.chunk_id for k in kept] == list(range(15))


def test_caps_chunks_per_article() -> None:
    ranked = [scored(i, article_id=1, relevance=100 - i) for i in range(5)]
    ranked += [scored(10 + i, article_id=2 + i, relevance=50 - i) for i in range(5)]
    kept = select_diverse(ranked, DiversityLimits(per_article=3))
    counts = Counter(k.chunk.article_id for k in kept)
    assert counts[1] == 3
    assert max(counts.values()) <= 3
    assert len(kept) == 8


def test_relevance_floor_applies_only_after_the_minimum() -> None:
    ranked = [scored(i, article_id=i, relevance=5.0 - i) for i in range(20)]
    limits = DiversityLimits(min_keep=8, max_keep=15, relevance_floor=0.0)
    kept = select_diverse(ranked, limits)
    assert len(kept) == 8
    assert min(k.relevance for k in kept) < 0.0


def test_floor_keeps_everything_relevant_up_to_max() -> None:
    ranked = [scored(i, article_id=i, relevance=20.0 - i) for i in range(20)]
    kept = select_diverse(ranked, DiversityLimits(max_keep=15, relevance_floor=0.0))
    assert len(kept) == 15


def test_article_without_an_abstract_chunk_gets_one_from_the_pool() -> None:
    ranked = [
        scored(1, article_id=1, relevance=9.0, section="Results"),
        scored(2, article_id=1, relevance=8.0, section="Methods"),
        scored(3, article_id=1, relevance=7.0, section="Discussion"),
        scored(4, article_id=1, relevance=1.0, section="Abstract-RESULTS"),
    ]
    kept = select_diverse(ranked, DiversityLimits(per_article=3))
    sections = [k.chunk.section for k in kept]
    assert "Abstract-RESULTS" in sections
    assert "Discussion" not in sections
    assert len(kept) == 3


def test_no_abstract_in_pool_leaves_selection_unchanged() -> None:
    ranked = [scored(1, article_id=1, relevance=9.0, section="Results")]
    assert [k.chunk.chunk_id for k in select_diverse(ranked)] == [1]


def test_empty_input() -> None:
    assert select_diverse([]) == []
