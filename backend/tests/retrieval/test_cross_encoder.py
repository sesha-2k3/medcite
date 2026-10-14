"""Runs the real MedCPT cross-encoder; skips unless `make models` has downloaded it."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.models.cross_encoder import MedCPTCrossEncoder, load_cross_encoder

pytestmark = pytest.mark.models


@pytest.fixture(scope="module")
def scorer() -> MedCPTCrossEncoder:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        return load_cross_encoder(settings, local_files_only=True)
    except OSError as exc:
        pytest.skip(f"MedCPT cross-encoder not downloaded ({exc}); run: make models")


def test_relevant_passage_scores_higher(scorer: MedCPTCrossEncoder) -> None:
    relevant, unrelated = scorer.score(
        "does curl training increase biceps size",
        [
            "Elbow flexor thickness increased after 8 weeks of dumbbell curl training.",
            "Metformin use was associated with slower decline in kidney function.",
        ],
    )
    assert relevant > unrelated


def test_batches_score_like_single_calls(scorer: MedCPTCrossEncoder) -> None:
    passages = [f"Passage {i} about resistance training." for i in range(20)]
    batched = scorer.score("resistance training", passages)
    single = [scorer.score("resistance training", [p])[0] for p in passages[:3]]
    assert len(batched) == 20
    assert batched[:3] == pytest.approx(single, abs=1e-3)


def test_no_passages(scorer: MedCPTCrossEncoder) -> None:
    assert scorer.score("anything", []) == []
