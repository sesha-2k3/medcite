"""Evidence grading.

A study's level comes from its design; modifiers flag weaknesses that lower confidence in it.
``suggest_rating`` applies simplified GRADE-style rules to the studies an answer cites. It
cannot judge inconsistency between findings, which the generator assesses from the text (M6).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.ingest.metadata import Design, OutcomeClass, StudyMetadata

Level = Literal["1", "2", "3", "4", "5", "M", "U"]
Modifier = Literal[
    "small_sample", "short_follow_up", "surrogate_outcome", "industry_funded", "ahead_of_print"
]
Rating = Literal["high", "moderate", "low", "very_low"]

LEVEL_BY_DESIGN: dict[Design, Level] = {
    "meta_analysis": "1",
    "systematic_review": "1",
    "guideline": "1",
    "rct": "2",
    "cohort": "3",
    "case_control": "3",
    "cross_sectional": "3",
    "case_series": "4",
    "case_report": "4",
    "narrative_review": "5",
    "commentary": "5",
    "mechanistic": "M",
    "animal": "M",
    "in_vitro": "M",
    "other": "U",
}
BADGES: dict[Level, str] = {
    "1": "Synthesis / Guideline",
    "2": "RCT",
    "3": "Observational",
    "4": "Descriptive",
    "5": "Narrative / Opinion",
    "M": "Mechanistic",
    "U": "Unclassified",
}
# Strongest first. Mechanistic and unclassified evidence never outranks a graded human study.
LEVEL_ORDER: tuple[Level, ...] = ("1", "2", "3", "4", "5", "U", "M")
STARTING_RATING: dict[Level, Rating] = {
    "1": "high",
    "2": "moderate",
    "3": "low",
    "4": "very_low",
    "5": "very_low",
    "U": "very_low",
    "M": "very_low",
}
RATINGS: tuple[Rating, ...] = ("high", "moderate", "low", "very_low")
DIRECT_TIERS = frozenset({1, 2})

_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(day|d|week|wk|month|mo|year|yr)s?\b", re.I)
_WEEKS_PER_UNIT = {"day": 1 / 7, "d": 1 / 7, "week": 1, "wk": 1, "month": 52 / 12, "mo": 52 / 12}
_WEEKS_PER_UNIT |= {"year": 52.0, "yr": 52.0}


@dataclass(frozen=True, slots=True)
class EvidenceThresholds:
    small_sample: int = 50
    short_follow_up_weeks: float = 12.0


@dataclass(frozen=True, slots=True)
class EvidenceGrade:
    level: Level
    badge: str
    design: Design
    modifiers: tuple[Modifier, ...]


@dataclass(frozen=True, slots=True)
class CitedEvidence:
    grade: EvidenceGrade
    tier: int | None


@dataclass(frozen=True, slots=True)
class RatingSuggestion:
    rating: Rating
    reasons: tuple[str, ...]


def grade_study(
    meta: StudyMetadata,
    *,
    publication_status: str | None = None,
    question_outcome: OutcomeClass | None = None,
    thresholds: EvidenceThresholds | None = None,
) -> EvidenceGrade:
    limits = thresholds or EvidenceThresholds()
    level = LEVEL_BY_DESIGN[meta.design]
    modifiers: list[Modifier] = []
    if meta.sample_size is not None and meta.sample_size < limits.small_sample:
        modifiers.append("small_sample")
    weeks = follow_up_weeks(meta.follow_up)
    if weeks is not None and weeks < limits.short_follow_up_weeks:
        modifiers.append("short_follow_up")
    if _is_surrogate(meta.outcome_class, question_outcome):
        modifiers.append("surrogate_outcome")
    if meta.funding in ("industry", "mixed"):
        modifiers.append("industry_funded")
    if publication_status == "aheadofprint":
        modifiers.append("ahead_of_print")
    return EvidenceGrade(level, BADGES[level], meta.design, tuple(modifiers))


def follow_up_weeks(text: str | None) -> float | None:
    """Longest duration stated in free text such as '6 weeks', '12-month', '2 yr'."""
    if not text:
        return None
    durations = [
        float(number) * _WEEKS_PER_UNIT[unit.lower()] for number, unit in _DURATION.findall(text)
    ]
    return max(durations) if durations else None


def suggest_rating(cited: Sequence[CitedEvidence]) -> RatingSuggestion:
    """Start from the best study that directly addresses the question, then downgrade."""
    if not cited:
        return RatingSuggestion("very_low", ("no qualifying evidence",))
    direct = [c for c in cited if c.tier in DIRECT_TIERS]
    pool = direct or list(cited)
    best = min(pool, key=lambda c: LEVEL_ORDER.index(c.grade.level))
    strongest = [c for c in pool if c.grade.level == best.grade.level]

    steps = 0
    reasons = [f"best evidence: {best.grade.badge}"]
    if not direct:
        steps += 1
        reasons.append("indirect: no study directly addresses the question")
    if all("surrogate_outcome" in c.grade.modifiers for c in strongest):
        steps += 1
        reasons.append("surrogate outcomes")
    if all("small_sample" in c.grade.modifiers for c in strongest):
        steps += 1
        reasons.append("small samples")

    start = RATINGS.index(STARTING_RATING[best.grade.level])
    return RatingSuggestion(RATINGS[min(start + steps, len(RATINGS) - 1)], tuple(reasons))


def _is_surrogate(study: OutcomeClass, question: OutcomeClass | None) -> bool:
    if study == "surrogate":
        return question != "surrogate"
    return question is not None and study not in ("unknown", question)
