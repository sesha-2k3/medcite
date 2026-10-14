from __future__ import annotations

from collections.abc import Sequence

from app.db.queries.chunks import ChunkView
from app.retrieval.hybrid_search import HybridHit
from app.retrieval.rerank import passage_text, rerank
from tests.retrieval.builders import view


class OverlapScorer:
    """Scores a passage by how many query words it contains."""

    def __init__(self) -> None:
        self.passages: list[str] = []

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        self.passages.extend(passages)
        words = set(query.lower().split())
        return [float(len(words & set(p.lower().split()))) for p in passages]


def pair(chunk_id: int, content: str) -> tuple[ChunkView, HybridHit]:
    return view(chunk_id, chunk_id, content=content), HybridHit(chunk_id, 0.01, 1, None)


def test_sorts_by_cross_encoder_score() -> None:
    scorer = OverlapScorer()
    candidates = [pair(1, "metformin kidney"), pair(2, "biceps curl growth"), pair(3, "curl")]
    ranked = rerank("biceps curl growth", candidates, scorer)
    assert [r.chunk.chunk_id for r in ranked] == [2, 3, 1]
    assert [r.relevance for r in ranked] == [3.0, 1.0, 0.0]


def test_passage_includes_title_for_context() -> None:
    chunk = view(7, 7, content="Thickness increased.")
    assert passage_text(chunk) == "Article 7 Thickness increased."


def test_empty_candidates_skip_the_model() -> None:
    scorer = OverlapScorer()
    assert rerank("q", [], scorer) == []
    assert scorer.passages == []
