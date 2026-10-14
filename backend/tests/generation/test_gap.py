from __future__ import annotations

import pytest

from app.generation.gap import (
    DEFAULT_LABEL,
    detect_evidence_gap,
    expanded_requested,
    gap_sentence,
    requested_evidence_label,
    states_gap,
)
from tests.generation.builders import creatine_passages, passage


def test_default_label_names_syntheses() -> None:
    assert requested_evidence_label([]) == DEFAULT_LABEL == "systematic review or meta-analysis"


def test_syntheses_are_requested_together() -> None:
    assert expanded_requested(["meta_analysis"]) == ("meta_analysis", "systematic_review")
    assert requested_evidence_label(["meta_analysis"]) == "meta-analysis or systematic review"
    assert requested_evidence_label(["rct"]) == "randomized controlled trial"


def test_gap_when_no_tier_one_article_has_the_requested_design() -> None:
    assert detect_evidence_gap(creatine_passages(), []) is True


def test_no_gap_when_a_tier_one_synthesis_is_kept() -> None:
    review = passage(41, 4, "Pooled results.", design="meta_analysis", tier=1)
    assert detect_evidence_gap([*creatine_passages(), review], ["systematic_review"]) is False


def test_a_synthesis_outside_tier_one_does_not_close_the_gap() -> None:
    review = passage(41, 4, "Pooled results.", design="meta_analysis", tier=3)
    assert detect_evidence_gap([review], ["meta_analysis"]) is True


def test_requested_primary_design_in_tier_one_closes_the_gap() -> None:
    trial = passage(41, 4, "Results.", design="rct", tier=1)
    assert detect_evidence_gap([trial], ["rct"]) is False


@pytest.mark.parametrize(
    ("sentence", "requested", "ok"),
    [
        ("No meta-analysis directly compares dumbbell and barbell curls.", ["meta_analysis"], True),
        ("There is no systematic review on this question.", ["meta_analysis"], True),
        ("We found no randomized trials of this drug.", ["rct"], True),
        ("Evidence syntheses on this question don't exist.", [], True),
        ("A meta-analysis found larger gains [C1].", ["meta_analysis"], False),
        ("No studies compared the two curls.", ["meta_analysis"], False),
    ],
)
def test_states_gap(sentence: str, requested: list[str], ok: bool) -> None:
    assert states_gap(sentence, requested) is ok  # type: ignore[arg-type]


def test_gap_sentence_names_the_requested_type() -> None:
    sentence = gap_sentence(requested_evidence_label(["meta_analysis"]))
    assert states_gap(sentence, ["meta_analysis"])
