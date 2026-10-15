"""Schemas for generation and verification.

``GeneratorDraft`` and ``JudgeVerdict`` are what the models return. ``Answer`` and
``VerificationResult`` are what the app keeps: code fills every field it can know without a
model (PMIDs, titles, designs, sample sizes, years, badges) and caps the rating.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.generation.citations import normalize_marker
from app.generation.plan import EvidenceType
from app.ingest.metadata import Design
from app.retrieval.evidence import Rating


def _snake(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower().replace(" ", "_").replace("-", "_")
    return value


class DraftRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    marker: str
    finding: str | None = None

    @field_validator("marker", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return normalize_marker(value) if isinstance(value, str) else value


class DraftRating(BaseModel):
    model_config = ConfigDict(extra="ignore")

    level: Rating
    justification: str = ""

    @field_validator("level", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return _snake(value)


class GeneratorDraft(BaseModel):
    """What the generator model returns (prompt generate@v1)."""

    model_config = ConfigDict(extra="ignore")

    direct_answer: str
    synthesis: str = ""
    evidence_rows: list[DraftRow] = Field(default_factory=list)
    rating: DraftRating


Verdict = Literal["supported", "partial", "unsupported"]


class ClaimVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    verdict: Verdict
    note: str | None = None

    @field_validator("verdict", mode="before")
    @classmethod
    def _normalize(cls, value: object) -> object:
        return _snake(value)

    @field_validator("id", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> object:
        return str(value).strip() if isinstance(value, int | str) else value


class JudgeVerdict(BaseModel):
    """What the judge model returns (prompt judge@v1)."""

    model_config = ConfigDict(extra="ignore")

    claims: list[ClaimVerdict] = Field(default_factory=list)
    gap_statement_ok: bool | None = None
    conflict_omitted: bool = False
    conflict_note: str | None = None


class Citation(BaseModel):
    marker: str
    pmid: int
    section: str | None
    chunk_id: int
    snippet: str


class EvidenceRow(BaseModel):
    pmid: int
    title: str
    design: Design
    n: int | None
    finding: str | None
    year: int | None
    badge: str
    markers: list[str]


class AnswerRating(BaseModel):
    level: Rating
    justification: str
    ceiling: Rating
    clamped: bool = False


class Answer(BaseModel):
    direct_answer: str
    synthesis: str
    evidence_table: list[EvidenceRow] = Field(default_factory=list)
    rating: AnswerRating
    citations: list[Citation] = Field(default_factory=list)
    evidence_gap: bool
    requested_evidence: list[EvidenceType] = Field(default_factory=list)
    requested_label: str
    # Rows the generator attached to markers that are not in the context; verification fails.
    unknown_row_markers: list[str] = Field(default_factory=list)
    prompt: str | None = None
    model: str | None = None


IssueKind = Literal[
    "unknown_citation",
    "uncited_sentence",
    "number_not_in_source",
    "gap_not_stated",
    "unsupported_claim",
    "conflict_omitted",
    "partially_supported",
    "rating_clamped",
    "judge_unavailable",
    "gap_statement_inserted",
    "regeneration_failed",
    "theme_omitted",
]
_ERRORS: tuple[IssueKind, ...] = (
    "unknown_citation",
    "uncited_sentence",
    "number_not_in_source",
    "gap_not_stated",
    "unsupported_claim",
    "conflict_omitted",
)
ERROR_KINDS: frozenset[IssueKind] = frozenset(_ERRORS)


class Issue(BaseModel):
    kind: IssueKind
    severity: Literal["error", "warning"]
    location: str
    message: str
    claim_id: str | None = None


def issue(kind: IssueKind, location: str, message: str, claim_id: str | None = None) -> Issue:
    severity: Literal["error", "warning"] = "error" if kind in ERROR_KINDS else "warning"
    return Issue(
        kind=kind, severity=severity, location=location, message=message, claim_id=claim_id
    )


JudgeStatus = Literal["ok", "skipped", "failed"]


class VerificationResult(BaseModel):
    passed: bool
    issues: list[Issue] = Field(default_factory=list)
    judge_status: JudgeStatus = "skipped"
    attempts: int = 1
    judge_prompt: str | None = None

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]
