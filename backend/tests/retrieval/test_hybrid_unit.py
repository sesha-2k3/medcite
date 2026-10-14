from __future__ import annotations

from app.retrieval.hybrid_search import HybridHit, keyword_query, merge_hits


def test_keyword_query_ors_unique_lowercased_words() -> None:
    query = keyword_query("Dumbbell vs barbell curl: dumbbell?")
    assert query == "dumbbell or vs or barbell or curl"


def test_keyword_query_drops_operator_words_and_keeps_hyphenated_terms() -> None:
    assert keyword_query("GLP-1 and muscle or not") == "glp-1 or muscle"


def test_keyword_query_of_punctuation_is_empty() -> None:
    assert keyword_query("?!") == ""


def hit(chunk_id: int, score: float) -> HybridHit:
    return HybridHit(chunk_id=chunk_id, rrf_score=score, dense_rank=1, sparse_rank=None)


def test_merge_keeps_best_score_per_chunk_and_sorts() -> None:
    merged = merge_hits([hit(1, 0.01), hit(2, 0.03)], [hit(1, 0.05), hit(3, 0.02)])
    assert [(h.chunk_id, h.rrf_score) for h in merged] == [(1, 0.05), (2, 0.03), (3, 0.02)]
