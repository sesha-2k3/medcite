"""Study metadata (design, population, sample size, outcome class, ...) for each article.

PubMed publication types decide the design when they are unambiguous. The planner-tier LLM fills
in everything else from the abstract, several articles per call to stay within free-tier rate
limits. Any failure falls back to what the publication types alone can tell us.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.errors import AppError
from app.generation.prompts import load_prompt
from app.ingest.pubmed_parser import ParsedArticle
from app.models.llm import ChatMessage, LLMClient, LLMRole, UsageMeter

logger = logging.getLogger(__name__)

Design = Literal[
    "meta_analysis",
    "systematic_review",
    "guideline",
    "rct",
    "cohort",
    "case_control",
    "cross_sectional",
    "case_series",
    "case_report",
    "narrative_review",
    "commentary",
    "mechanistic",
    "animal",
    "in_vitro",
    "other",
]
AgeGroup = Literal["children", "adults", "older_adults", "mixed", "unknown"]
OutcomeClass = Literal["clinical", "physiological", "surrogate", "patient_reported", "unknown"]
Funding = Literal["industry", "public", "mixed", "not_stated"]
Source = Literal["llm", "publication_types"]

# Ordered by precedence: a record tagged both "Meta-Analysis" and "Review" is a meta-analysis.
DEFINITIVE_DESIGNS: tuple[tuple[str, Design], ...] = (
    ("Meta-Analysis", "meta_analysis"),
    ("Systematic Review", "systematic_review"),
    ("Practice Guideline", "guideline"),
    ("Guideline", "guideline"),
    ("Randomized Controlled Trial", "rct"),
    ("Case Reports", "case_report"),
    ("Comment", "commentary"),
    ("Editorial", "commentary"),
    ("Letter", "commentary"),
)
# "Review" alone may hide an untagged systematic review, so it only applies without the LLM.
FALLBACK_DESIGNS: tuple[tuple[str, Design], ...] = (("Review", "narrative_review"),)

# NIH and US-government support is public by definition. "Non-U.S. Gov't" also covers
# foundations and companies, so it says nothing either way.
PUBLIC_FUNDING_PREFIXES = ("Research Support, N.I.H.", "Research Support, U.S. Gov't")

VOCABULARY: dict[str, tuple[tuple[str, ...], str]] = {
    "design": (get_args(Design), "other"),
    "age_group": (get_args(AgeGroup), "unknown"),
    "outcome_class": (get_args(OutcomeClass), "unknown"),
    "funding": (get_args(Funding), "not_stated"),
}

PROMPT = load_prompt("extract_metadata", "v2")
MAX_TOKENS_PER_ARTICLE = 400


class StudyMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    design: Design = "other"
    population: str | None = None
    age_group: AgeGroup = "unknown"
    sample_size: int | None = Field(default=None, ge=0)
    intervention: str | None = None
    comparator: str | None = None
    primary_outcome: str | None = None
    outcome_class: OutcomeClass = "unknown"
    follow_up: str | None = None
    funding: Funding = "not_stated"
    source: Source = "llm"
    prompt: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _unknown_if_invalid(cls, data: Any) -> Any:
        """A label outside a field's vocabulary (the model once gave outcome_class
        "mechanistic") becomes that field's unknown value instead of failing the whole batch.
        """
        if not isinstance(data, dict):
            return data
        fixed = dict(data)
        for key, (allowed, fallback) in VOCABULARY.items():
            if key in fixed and fixed[key] not in allowed:
                fixed[key] = fallback
        return fixed


class _Extracted(StudyMetadata):
    pmid: int


class _Batch(BaseModel):
    articles: list[_Extracted]


def design_from_pub_types(pub_types: Sequence[str], *, include_fallback: bool) -> Design | None:
    table = DEFINITIVE_DESIGNS + (FALLBACK_DESIGNS if include_fallback else ())
    present = set(pub_types)
    return next((design for pub_type, design in table if pub_type in present), None)


def funding_from_pub_types(pub_types: Sequence[str]) -> Funding | None:
    return "public" if any(p.startswith(PUBLIC_FUNDING_PREFIXES) for p in pub_types) else None


def fallback_metadata(article: ParsedArticle) -> StudyMetadata:
    design = design_from_pub_types(article.pub_types, include_fallback=True)
    return StudyMetadata(
        design=design or "other",
        funding=funding_from_pub_types(article.pub_types) or "not_stated",
        source="publication_types",
    )


class MetadataExtractor:
    def __init__(self, llm: LLMClient, batch_size: int = 5) -> None:
        self._llm = llm
        self._batch_size = batch_size

    async def extract(
        self, articles: Sequence[ParsedArticle], meter: UsageMeter | None = None
    ) -> dict[int, StudyMetadata]:
        """Metadata for every article, keyed by PMID. Never raises for LLM problems."""
        result = {a.pmid: fallback_metadata(a) for a in articles}
        with_abstract = [a for a in articles if a.has_abstract]
        for start in range(0, len(with_abstract), self._batch_size):
            batch = with_abstract[start : start + self._batch_size]
            result.update(await self._extract_batch(batch, meter))
        return result

    async def _extract_batch(
        self, batch: Sequence[ParsedArticle], meter: UsageMeter | None
    ) -> dict[int, StudyMetadata]:
        try:
            response = await self._llm.complete_json(
                LLMRole.PLANNER,
                [
                    ChatMessage("system", PROMPT.text),
                    ChatMessage("user", _render(batch)),
                ],
                _Batch,
                max_tokens=MAX_TOKENS_PER_ARTICLE * len(batch) + 1024,
                meter=meter,
            )
        except AppError as exc:
            logger.warning(
                "metadata_fallback",
                extra={"pmids": [a.pmid for a in batch], "error": exc.message},
            )
            return {}

        by_pmid = {a.pmid: a for a in batch}
        extracted: dict[int, StudyMetadata] = {}
        for item in response.articles:
            article = by_pmid.get(item.pmid)
            if article is None:
                continue
            definitive = design_from_pub_types(article.pub_types, include_fallback=False)
            fields = item.model_dump(exclude={"pmid", "source", "prompt"})
            fields["design"] = definitive or item.design
            if item.funding == "not_stated":
                fields["funding"] = funding_from_pub_types(article.pub_types) or "not_stated"
            extracted[item.pmid] = StudyMetadata(**fields, source="llm", prompt=PROMPT.id)
        missing = by_pmid.keys() - extracted.keys()
        if missing:
            logger.warning("metadata_missing_from_response", extra={"pmids": sorted(missing)})
        return extracted


def _render(batch: Sequence[ParsedArticle]) -> str:
    payload = [
        {
            "pmid": a.pmid,
            "title": a.title,
            "publication_types": list(a.pub_types),
            "abstract": "\n".join(
                f"{s.label}: {s.text}" if s.label else s.text for s in a.abstract
            ),
        }
        for a in batch
    ]
    return json.dumps(payload, ensure_ascii=False)
