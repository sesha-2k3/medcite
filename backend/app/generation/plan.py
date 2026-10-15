"""Plan a question: route it, frame it as PICO, and build tiered PubMed queries.

The planner model returns concepts (MeSH headings and free-text synonyms per PICO element);
code turns them into the four tiers of the evidence ladder:

    1  exact evidence           every concept, restricted to the requested study type
                                (syntheses and guidelines when none is requested)
    2  direct primary studies   every concept, excluding reviews and commentary
    3  related syntheses        broader intervention and outcome in the same population and
                                condition, any body site, reviews only
    4  mechanistic or surrogate broader intervention at the body site, surrogate outcomes,
                                same condition

Discovery has one tier: population, condition, intervention, site and outcome within the
date window.

If the model fails, or its concepts produce an invalid query, a keyword plan built from the
question itself takes over, so a question is never left without a search.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.errors import AppError
from app.generation.mesh import MeshChecker, TermSearcher
from app.generation.prompts import load_prompt
from app.generation.pubmed_query import (
    Terms,
    any_of,
    compose,
    concept_clause,
    date_filter,
    natural_order,
    validate_query,
)
from app.ingest.metadata import OutcomeClass
from app.models.llm import ChatMessage, LLMClient, LLMRole, UsageMeter

logger = logging.getLogger(__name__)

PROMPT = load_prompt("plan", "v4")
BROADEN_PROMPT = load_prompt("plan_broaden", "v1")

Mode = Literal["question", "discovery", "hybrid", "out_of_scope"]
EvidenceType = Literal[
    "meta_analysis",
    "systematic_review",
    "guideline",
    "rct",
    "cohort",
    "case_control",
    "cross_sectional",
    "case_report",
]

EVIDENCE_FILTERS: dict[EvidenceType, tuple[str, ...]] = {
    "meta_analysis": ('"meta-analysis"[pt]',),
    "systematic_review": ('"systematic review"[pt]',),
    "guideline": ('"guideline"[pt]', '"practice guideline"[pt]'),
    "rct": ('"randomized controlled trial"[pt]',),
    "cohort": ('"cohort studies"[mh]',),
    "case_control": ('"case-control studies"[mh]',),
    "cross_sectional": ('"cross-sectional studies"[mh]',),
    "case_report": ('"case reports"[pt]',),
}
BEST_EVIDENCE: tuple[EvidenceType, ...] = ("meta_analysis", "systematic_review", "guideline")
# Asking for either kind of synthesis is satisfied by both: most meta-analyses sit inside a
# systematic review, and PubMed tags them inconsistently.
SYNTHESES: tuple[EvidenceType, ...] = ("meta_analysis", "systematic_review")
REVIEWS = ('"meta-analysis"[pt]', '"systematic review"[pt]', '"review"[pt]')
NOT_PRIMARY = (*REVIEWS, '"comment"[pt]', '"editorial"[pt]', '"letter"[pt]')

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "not",
        "of",
        "on",
        "or",
        "than",
        "that",
        "the",
        "there",
        "this",
        "to",
        "vs",
        "versus",
        "was",
        "what",
        "when",
        "which",
        "who",
        "why",
        "will",
        "with",
    ]
)
# Words naming a study type, not the topic; tier filters already handle study type.
_STUDY_WORDS = frozenset(
    [
        "meta-analysis",
        "meta-analyses",
        "metaanalysis",
        "systematic",
        "review",
        "reviews",
        "study",
        "studies",
        "trial",
        "trials",
        "evidence",
        "research",
    ]
)
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-]*")


class Concept(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mesh: list[str] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)

    def terms(self) -> Terms:
        return Terms(mesh=self.mesh, text=self.text)

    @field_validator("mesh", "text")
    @classmethod
    def _unique(cls, terms: list[str]) -> list[str]:
        """First spelling of each term, ignoring case; the model and rejected MeSH headings
        moved into text terms can both repeat a term.
        """
        seen: set[str] = set()
        unique: list[str] = []
        for term in (t.strip() for t in terms):
            if term and term.lower() not in seen:
                seen.add(term.lower())
                unique.append(term)
        return unique


class Concepts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    population: Concept | None = None
    condition: Concept | None = None  # the disease or health state; never part of population
    intervention: Concept | None = None
    comparison: Concept | None = None
    outcome: Concept | None = None
    site: Concept | None = None
    broader_intervention: Concept | None = None
    surrogate_outcome: Concept | None = None


class DateWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    start: date = Field(alias="from")
    end: date | None = Field(default=None, alias="to")


class Tier(BaseModel):
    level: int
    purpose: str
    pubmed_query: str


class Plan(BaseModel):
    """The planner output of build plan section 7.5, plus the concepts and its provenance."""

    mode: Mode
    population: str | None = None
    condition: str | None = None
    intervention: str | None = None
    comparison: str | None = None
    outcome: str | None = None
    outcome_class: OutcomeClass = "unknown"
    requested_evidence: list[EvidenceType] = Field(default_factory=list)
    date_range: DateWindow | None = None
    standalone_query: str
    tiers: list[Tier] = Field(default_factory=list)
    concepts: Concepts | None = None
    rejected_mesh: list[str] = Field(default_factory=list)
    broadened: bool = False
    source: Literal["llm", "fallback"] = "llm"
    prompt: str | None = None


class _ModelPlan(BaseModel):
    """What the planner model is asked to return."""

    model_config = ConfigDict(extra="ignore")

    mode: Mode
    population: str | None = None
    condition: str | None = None
    intervention: str | None = None
    comparison: str | None = None
    outcome: str | None = None
    outcome_class: OutcomeClass = "unknown"
    requested_evidence: list[EvidenceType] = Field(default_factory=list)
    date_range: DateWindow | None = None
    standalone_query: str
    concepts: Concepts = Field(default_factory=Concepts)

    @field_validator(
        "population", "condition", "intervention", "comparison", "outcome", mode="before"
    )
    @classmethod
    def _drop_placeholders(cls, value: object) -> object:
        if isinstance(value, str) and value.strip(" .") == "":
            return None
        return value


class Planner:
    def __init__(self, llm: LLMClient, searcher: TermSearcher | None = None) -> None:
        """With a ``searcher`` (the PubMed client), MeSH headings are verified and an empty
        evidence ladder is retried once with broader terms; without one, neither happens.
        """
        self._llm = llm
        self._searcher = searcher
        self._mesh = MeshChecker(searcher) if searcher is not None else None

    async def plan(
        self,
        question: str,
        *,
        history: Sequence[str] = (),
        today: date | None = None,
        window: DateWindow | None = None,
        meter: UsageMeter | None = None,
    ) -> Plan:
        """``history`` holds earlier questions in this conversation, oldest first.
        ``window`` (from a discovery request) overrides any date range in the question.
        """
        today = today or date.today()
        conversation = [
            ChatMessage("system", PROMPT.text),
            ChatMessage("user", _render(question, history, today)),
        ]
        drafted = await self._draft(conversation, meter)
        if drafted is None:
            return fallback_plan(question, window=window)
        drafted = _adjusted(drafted, window, today)
        plan = await self._build(drafted)
        if plan is None:
            return fallback_plan(drafted.standalone_query or question, window=window)
        if not await self._direct_tiers_empty(plan):
            return plan

        retry = [
            *conversation,
            ChatMessage("assistant", drafted.model_dump_json(by_alias=True)),
            ChatMessage("user", BROADEN_PROMPT.text),
        ]
        broader_draft = await self._draft(retry, meter)
        if broader_draft is None:
            return plan
        broader = await self._build(_adjusted(broader_draft, window, today))
        if broader is None:
            return plan
        logger.info("plan_broadened", extra={"question": question})
        return broader.model_copy(update={"broadened": True})

    async def _draft(
        self, messages: Sequence[ChatMessage], meter: UsageMeter | None
    ) -> _ModelPlan | None:
        try:
            return await self._llm.complete_json(
                LLMRole.PLANNER, messages, _ModelPlan, max_tokens=3000, meter=meter
            )
        except AppError as exc:
            logger.warning("plan_draft_failed", extra={"reason": exc.message})
            return None

    async def _build(self, drafted: _ModelPlan) -> Plan | None:
        """Verify MeSH, build the tiers, and reject the plan if any query is malformed."""
        concepts, rejected = await self._verified(drafted.concepts)
        plan = build_plan(drafted.model_copy(update={"concepts": concepts}))
        plan = plan.model_copy(update={"rejected_mesh": rejected})
        problems = {t.level: validate_query(t.pubmed_query) for t in plan.tiers}
        if plan.mode != "out_of_scope" and (not plan.tiers or any(problems.values())):
            logger.warning("plan_invalid_queries", extra={"problems": problems})
            return None
        return plan

    async def _direct_tiers_empty(self, plan: Plan) -> bool:
        """Whether tiers 1 and 2 both match nothing, the case that would be reported as an
        evidence gap. Unknown (no searcher, or PubMed unreachable) counts as not empty.
        """
        if self._searcher is None or plan.mode not in ("question", "hybrid"):
            return False
        if len(plan.tiers) < 2:
            return False
        try:
            for tier in plan.tiers[:2]:
                if (await self._searcher.esearch(tier.pubmed_query, retmax=0)).count:
                    return False
        except AppError as exc:
            logger.warning("tier_count_skipped", extra={"reason": exc.message})
            return False
        return True

    async def _verified(self, concepts: Concepts) -> tuple[Concepts, list[str]]:
        """Turn invented MeSH headings into text terms. Fails open: if PubMed can't be
        reached, headings stay as given, and their paired text terms still keep recall.
        """
        if self._mesh is None:
            return concepts, []
        headings = [m for c in _each(concepts) for m in c.mesh]
        try:
            unknown = await self._mesh.unknown(headings)
        except AppError as exc:
            logger.warning("mesh_check_skipped", extra={"reason": exc.message})
            return concepts, []
        if not unknown:
            return concepts, []
        logger.info("mesh_rejected", extra={"headings": sorted(unknown)})
        return without_headings(concepts, unknown), sorted(unknown)


def _adjusted(drafted: _ModelPlan, window: DateWindow | None, today: date) -> _ModelPlan:
    if window is not None:
        drafted = drafted.model_copy(update={"date_range": window, "mode": "discovery"})
    return _open_ended(drafted, today)


def without_headings(concepts: Concepts, unknown: set[str]) -> Concepts:
    """Move ``unknown`` headings from each concept's MeSH list into its text terms."""
    updates: dict[str, Concept] = {}
    for name in Concepts.model_fields:
        concept: Concept | None = getattr(concepts, name)
        if concept is None or not unknown.intersection(concept.mesh):
            continue
        kept = [m for m in concept.mesh if m not in unknown]
        moved = [natural_order(m) for m in concept.mesh if m in unknown]
        updates[name] = Concept(mesh=kept, text=[*concept.text, *moved])
    return concepts.model_copy(update=updates)


def _each(concepts: Concepts) -> list[Concept]:
    return [c for name in Concepts.model_fields if (c := getattr(concepts, name)) is not None]


def _open_ended(drafted: _ModelPlan, today: date) -> _ModelPlan:
    """A window ending today or later stays open, so records added today are not cut off."""
    window = drafted.date_range
    if window is None or window.end is None or window.end < today - timedelta(days=1):
        return drafted
    return drafted.model_copy(update={"date_range": DateWindow(start=window.start)})


def build_plan(drafted: _ModelPlan) -> Plan:
    plan = Plan.model_validate({**drafted.model_dump(), "prompt": PROMPT.id})
    if drafted.mode == "out_of_scope":
        return plan.model_copy(update={"tiers": []})
    tiers = _discovery_tiers(drafted) if drafted.mode == "discovery" else _question_tiers(drafted)
    return plan.model_copy(update={"tiers": tiers})


def _question_tiers(drafted: _ModelPlan) -> list[Tier]:
    c = drafted.concepts
    population = _clause(c.population)
    condition = _clause(c.condition)
    intervention = _clause(c.intervention)
    comparison = _clause(c.comparison)
    outcome = _clause(c.outcome)
    site = _clause(c.site)
    broader = _clause(c.broader_intervention) or intervention
    surrogate = _clause(c.surrogate_outcome) or outcome
    if intervention is None and outcome is None:
        return []

    dates = _dates(drafted)
    core = _present(population, condition, intervention, comparison, site, outcome)
    exact = any_of(f for kind in _tier_one_types(drafted) for f in EVIDENCE_FILTERS[kind])
    return [
        Tier(
            level=1,
            purpose="exact evidence",
            pubmed_query=compose(core, include=exact, dates=dates),
        ),
        Tier(
            level=2,
            purpose="direct primary studies",
            pubmed_query=compose(core, exclude=any_of(NOT_PRIMARY), dates=dates),
        ),
        Tier(
            level=3,
            purpose="related syntheses",
            pubmed_query=compose(
                _present(population, condition, broader, outcome),
                include=any_of(REVIEWS),
                dates=dates,
            ),
        ),
        Tier(
            level=4,
            purpose="mechanistic or surrogate",
            pubmed_query=compose(_present(condition, broader, site, surrogate), dates=dates),
        ),
    ]


def _tier_one_types(drafted: _ModelPlan) -> list[EvidenceType]:
    requested = list(drafted.requested_evidence) or list(BEST_EVIDENCE)
    if any(kind in SYNTHESES for kind in requested):
        requested += [kind for kind in SYNTHESES if kind not in requested]
    return requested


def _discovery_tiers(drafted: _ModelPlan) -> list[Tier]:
    c = drafted.concepts
    topic = _present(
        *(_clause(x) for x in (c.population, c.condition, c.intervention, c.site, c.outcome))
    )
    if not topic:
        return []
    return [
        Tier(level=1, purpose="recent research", pubmed_query=compose(topic, dates=_dates(drafted)))
    ]


def fallback_plan(question: str, *, window: DateWindow | None = None) -> Plan:
    """Keyword plan: PubMed's own term mapping on the question's content words."""
    skip = _STOPWORDS | _STUDY_WORDS
    words = [w for w in _WORD.findall(question) if w.lower() not in skip]
    keywords = " ".join(dict.fromkeys(words)) or question
    dates = date_filter(window.start, window.end, "edat") if window else None
    clauses = [f"({keywords})"]
    if window is not None:
        tiers = [
            Tier(level=1, purpose="recent research", pubmed_query=compose(clauses, dates=dates))
        ]
    else:
        tiers = [
            Tier(
                level=1,
                purpose="exact evidence",
                pubmed_query=compose(
                    clauses, include=any_of(f for k in BEST_EVIDENCE for f in EVIDENCE_FILTERS[k])
                ),
            ),
            Tier(level=2, purpose="direct primary studies", pubmed_query=compose(clauses)),
        ]
    return Plan(
        mode="discovery" if window is not None else "question",
        standalone_query=question,
        date_range=window,
        tiers=tiers,
        source="fallback",
    )


def _clause(concept: Concept | None) -> str | None:
    return concept_clause(concept.terms()) if concept else None


def _present(*clauses: str | None) -> list[str]:
    return [clause for clause in clauses if clause]


def _dates(drafted: _ModelPlan) -> str | None:
    window = drafted.date_range
    if window is None:
        return None
    field = "edat" if drafted.mode in ("discovery", "hybrid") else "dp"
    return date_filter(window.start, window.end, field)


def _render(question: str, history: Sequence[str], today: date) -> str:
    return json.dumps(
        {"today": today.isoformat(), "earlier_questions": list(history), "question": question},
        ensure_ascii=False,
    )
