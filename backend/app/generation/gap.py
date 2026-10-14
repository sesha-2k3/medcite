"""The evidence gap: whether the requested kind of evidence was found, and how to say so.

A gap exists when no retrieved passage comes from a tier-1 article whose design matches the
requested type. Meta-analyses and systematic reviews satisfy each other; with no request, the
best evidence (syntheses and guidelines) is what was searched for.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.generation.citations import strip_markers
from app.generation.plan import BEST_EVIDENCE, SYNTHESES, EvidenceType
from app.retrieval.retriever import EvidencePassage

DEFAULT_LABEL = "systematic review or meta-analysis"
LABELS: dict[EvidenceType, str] = {
    "meta_analysis": "meta-analysis",
    "systematic_review": "systematic review",
    "guideline": "guideline",
    "rct": "randomized controlled trial",
    "cohort": "cohort study",
    "case_control": "case-control study",
    "cross_sectional": "cross-sectional study",
    "case_report": "case report",
}
_NAMES: dict[EvidenceType, re.Pattern[str]] = {
    "meta_analysis": re.compile(r"meta-?analy|synthes[ie]s", re.I),
    "systematic_review": re.compile(r"systematic(?:ally)?\s+review|synthes[ie]s", re.I),
    "guideline": re.compile(r"guideline", re.I),
    "rct": re.compile(r"randomi[sz]ed|\bRCTs?\b|controlled\s+trial", re.I),
    "cohort": re.compile(r"cohort", re.I),
    "case_control": re.compile(r"case[-\s]control", re.I),
    "cross_sectional": re.compile(r"cross[-\s]sectional", re.I),
    "case_report": re.compile(r"case\s+reports?", re.I),
}
_NEGATION = re.compile(
    r"\b(?:no|not|none|nor|neither|without|lacks?|lacking|absent|absence|unavailable|"
    r"zero|never)\b|n't\b",
    re.I,
)


def expanded_requested(requested: Sequence[EvidenceType]) -> tuple[EvidenceType, ...]:
    kinds = list(dict.fromkeys(requested)) or list(BEST_EVIDENCE)
    if any(kind in SYNTHESES for kind in kinds):
        kinds += [kind for kind in SYNTHESES if kind not in kinds]
    return tuple(kinds)


def requested_evidence_label(requested: Sequence[EvidenceType]) -> str:
    """'meta-analysis or systematic review'; the default when nothing was requested."""
    if not requested:
        return DEFAULT_LABEL
    return " or ".join(LABELS[kind] for kind in expanded_requested(requested))


def detect_evidence_gap(
    passages: Sequence[EvidencePassage], requested: Sequence[EvidenceType]
) -> bool:
    kinds = set(expanded_requested(requested))
    return not any(p.tier == 1 and p.grade.design in kinds for p in passages)


def states_gap(sentence: str, requested: Sequence[EvidenceType]) -> bool:
    """A gap statement names the requested evidence type and negates it."""
    text = strip_markers(sentence)
    names_type = any(_NAMES[kind].search(text) for kind in expanded_requested(requested))
    return names_type and _NEGATION.search(text) is not None


def gap_sentence(label: str) -> str:
    """The fallback statement code inserts when the generator never states the gap."""
    return f"No {label} addressing this question was found in PubMed."
