from __future__ import annotations

from typing import get_args

import pytest

from app.ingest.metadata import Design, StudyMetadata
from app.retrieval.evidence import (
    BADGES,
    LEVEL_BY_DESIGN,
    CitedEvidence,
    EvidenceGrade,
    EvidenceThresholds,
    follow_up_weeks,
    grade_study,
    suggest_rating,
)


@pytest.mark.parametrize(
    ("design", "level", "badge"),
    [
        ("meta_analysis", "1", "Synthesis / Guideline"),
        ("systematic_review", "1", "Synthesis / Guideline"),
        ("guideline", "1", "Synthesis / Guideline"),
        ("rct", "2", "RCT"),
        ("cohort", "3", "Observational"),
        ("case_control", "3", "Observational"),
        ("cross_sectional", "3", "Observational"),
        ("case_series", "4", "Descriptive"),
        ("case_report", "4", "Descriptive"),
        ("narrative_review", "5", "Narrative / Opinion"),
        ("commentary", "5", "Narrative / Opinion"),
        ("mechanistic", "M", "Mechanistic"),
        ("animal", "M", "Mechanistic"),
        ("in_vitro", "M", "Mechanistic"),
        ("other", "U", "Unclassified"),
    ],
)
def test_each_design_maps_to_its_level_and_badge(design: Design, level: str, badge: str) -> None:
    grade = grade_study(StudyMetadata(design=design))
    assert (grade.level, grade.badge, grade.modifiers) == (level, badge, ())


def test_every_design_has_a_level_and_every_level_a_badge() -> None:
    assert set(LEVEL_BY_DESIGN) == set(get_args(Design))
    assert set(LEVEL_BY_DESIGN.values()) <= set(BADGES)


@pytest.mark.parametrize(
    ("meta", "status", "modifier"),
    [
        (StudyMetadata(sample_size=18), None, "small_sample"),
        (StudyMetadata(follow_up="6 weeks"), None, "short_follow_up"),
        (StudyMetadata(outcome_class="surrogate"), None, "surrogate_outcome"),
        (StudyMetadata(funding="industry"), None, "industry_funded"),
        (StudyMetadata(funding="mixed"), None, "industry_funded"),
        (StudyMetadata(), "aheadofprint", "ahead_of_print"),
    ],
)
def test_each_modifier_is_flagged(meta: StudyMetadata, status: str | None, modifier: str) -> None:
    assert grade_study(meta, publication_status=status).modifiers == (modifier,)


@pytest.mark.parametrize(
    "meta",
    [
        StudyMetadata(sample_size=50),
        StudyMetadata(sample_size=None),
        StudyMetadata(follow_up="12 weeks"),
        StudyMetadata(follow_up="2 years"),
        StudyMetadata(follow_up="not reported"),
        StudyMetadata(funding="public"),
        StudyMetadata(funding="not_stated"),
    ],
)
def test_no_modifier_at_or_beyond_thresholds(meta: StudyMetadata) -> None:
    assert grade_study(meta, publication_status="ppublish").modifiers == ()


def test_thresholds_are_configurable() -> None:
    meta = StudyMetadata(sample_size=80, follow_up="16 weeks")
    strict = EvidenceThresholds(small_sample=100, short_follow_up_weeks=26)
    assert grade_study(meta, thresholds=strict).modifiers == ("small_sample", "short_follow_up")


@pytest.mark.parametrize(
    ("study", "question", "flagged"),
    [
        ("physiological", "physiological", False),
        ("physiological", "clinical", True),
        ("surrogate", "physiological", True),
        ("surrogate", "surrogate", False),
        ("unknown", "clinical", False),
        ("clinical", None, False),
        ("clinical", "unknown", False),
        ("surrogate", "unknown", True),
    ],
)
def test_surrogate_outcome_is_relative_to_the_question(
    study: str, question: str | None, flagged: bool
) -> None:
    meta = StudyMetadata(outcome_class=study)  # type: ignore[arg-type]
    grade = grade_study(meta, question_outcome=question)  # type: ignore[arg-type]
    assert ("surrogate_outcome" in grade.modifiers) is flagged


@pytest.mark.parametrize(
    ("text", "weeks"),
    [
        ("6 weeks", 6.0),
        ("8-week intervention", 8.0),
        ("12 months", 52.0),
        ("2 yr", 104.0),
        ("14 days", 2.0),
        ("6 weeks training plus 3 months follow-up", 13.0),
        ("not reported", None),
        (None, None),
    ],
)
def test_follow_up_weeks(text: str | None, weeks: float | None) -> None:
    result = follow_up_weeks(text)
    if weeks is None:
        assert result is None
    else:
        assert result == pytest.approx(weeks)


def cited(design: Design, tier: int | None, *modifiers: str) -> CitedEvidence:
    level = LEVEL_BY_DESIGN[design]
    grade = EvidenceGrade(level, BADGES[level], design, modifiers)  # type: ignore[arg-type]
    return CitedEvidence(grade, tier)


def test_rating_starts_from_best_direct_level() -> None:
    assert suggest_rating([cited("meta_analysis", 1), cited("rct", 2)]).rating == "high"
    assert suggest_rating([cited("rct", 2)]).rating == "moderate"
    assert suggest_rating([cited("cohort", 2)]).rating == "low"
    assert suggest_rating([cited("case_report", 2)]).rating == "very_low"


def test_indirect_surrogate_and_small_downgrade_one_step_each() -> None:
    assert suggest_rating([cited("meta_analysis", 3)]).rating == "moderate"
    assert suggest_rating([cited("meta_analysis", 1, "surrogate_outcome")]).rating == "moderate"
    assert suggest_rating([cited("meta_analysis", 1, "small_sample")]).rating == "moderate"
    both = cited("meta_analysis", 1, "small_sample", "surrogate_outcome")
    assert suggest_rating([both]).rating == "low"


def test_downgrades_need_every_strongest_study_to_share_the_weakness() -> None:
    small, large = cited("rct", 2, "small_sample"), cited("rct", 2)
    assert suggest_rating([small, large]).rating == "moderate"


def test_rating_never_falls_below_very_low() -> None:
    worst = cited("commentary", 4, "small_sample", "surrogate_outcome")
    assert suggest_rating([worst]).rating == "very_low"


def test_no_evidence_is_very_low() -> None:
    assert suggest_rating([]).rating == "very_low"


def test_dumbbell_versus_barbell_reference_scenario() -> None:
    """Gap case: no direct study, only small related trials and acute EMG work."""
    rating = suggest_rating(
        [
            cited("rct", 3, "small_sample", "short_follow_up"),
            cited("rct", 3, "small_sample"),
            cited("mechanistic", 4, "small_sample", "surrogate_outcome"),
        ]
    )
    assert rating.rating == "very_low"
    assert "indirect: no study directly addresses the question" in rating.reasons
