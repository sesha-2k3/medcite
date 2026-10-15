"""Write a cited answer from numbered passages (generation contract, handoff section 7.8).

The model writes the language: direct answer, synthesis, one finding per cited article, and a
rating with its justification. Code supplies everything else in the evidence table, resolves
markers to citations, and caps the rating at what ``suggest_rating`` allows for the cited
studies.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.generation.answer import (
    Answer,
    AnswerRating,
    Citation,
    EvidenceRow,
    GeneratorDraft,
    Issue,
)
from app.generation.citations import marker_ids, marker_number, normalize_text
from app.generation.context import ContextItem, EvidenceContext, rating_ceiling
from app.generation.gap import requested_evidence_label
from app.generation.plan import EvidenceType, Plan
from app.generation.prompts import load_prompt
from app.models.llm import ChatMessage, LLMClient, LLMRole, UsageMeter
from app.retrieval.evidence import RATINGS

PROMPT = load_prompt("generate", "v2")
MAX_FEEDBACK_ITEMS = 12


class Generator:
    def __init__(self, llm: LLMClient, *, max_tokens: int = 2500) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    async def generate(
        self,
        question: str,
        context: EvidenceContext,
        *,
        plan: Plan | None = None,
        evidence_gap: bool,
        feedback: Sequence[Issue] = (),
        meter: UsageMeter | None = None,
    ) -> Answer:
        """``feedback`` lists the failed checks of a previous attempt, for the one retry.

        Fails closed: ``LLMOutputError`` and provider errors propagate, since there is no
        answer without the model's structured output.
        """
        requested: list[EvidenceType] = list(plan.requested_evidence) if plan else []
        messages = [
            ChatMessage("system", PROMPT.text),
            ChatMessage(
                "user",
                render_request(
                    question,
                    context,
                    plan=plan,
                    evidence_gap=evidence_gap,
                    feedback=feedback,
                ),
            ),
        ]
        draft = await self._llm.complete_json(
            LLMRole.GENERATOR,
            messages,
            GeneratorDraft,
            max_tokens=self._max_tokens,
            meter=meter,
        )
        return assemble_answer(
            draft,
            context,
            evidence_gap=evidence_gap,
            requested=requested,
            model=self._llm.model_for(LLMRole.GENERATOR),
        )


def render_request(
    question: str,
    context: EvidenceContext,
    *,
    plan: Plan | None,
    evidence_gap: bool,
    feedback: Sequence[Issue] = (),
) -> str:
    label = requested_evidence_label(plan.requested_evidence if plan else [])
    ceiling = rating_ceiling(context.items)
    lines = [f"QUESTION: {question}"]
    if plan is not None:
        fields = (
            ("population", plan.population),
            ("condition", plan.condition),
            ("intervention", plan.intervention),
            ("comparison", plan.comparison),
            ("outcome", plan.outcome),
        )
        framing = "; ".join(f"{name}: {value}" for name, value in fields if value)
        if framing:
            lines.append(f"FRAMING: {framing}; outcome class: {plan.outcome_class}")
    lines.append(f"REQUESTED EVIDENCE: {label}")
    if evidence_gap:
        lines.append(
            f"EVIDENCE GAP: yes. Open direct_answer by stating that no {label} addressing "
            "this question was found."
        )
    else:
        lines.append("EVIDENCE GAP: no")
    lines.append(f"RATING CEILING: {ceiling.rating} ({'; '.join(ceiling.reasons)})")
    if feedback:
        lines.append("YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS. Fix every one:")
        lines += [f"- {item.location}: {item.message}" for item in feedback[:MAX_FEEDBACK_ITEMS]]
    lines += ["", "PASSAGES:", context.text]
    return "\n".join(lines)


def assemble_answer(
    draft: GeneratorDraft,
    context: EvidenceContext,
    *,
    evidence_gap: bool,
    requested: Sequence[EvidenceType],
    model: str | None,
) -> Answer:
    known = context.by_marker
    findings: dict[int, list[str]] = {}
    row_markers: list[str] = []
    unknown_rows: list[str] = []
    for row in draft.evidence_rows:
        item = known.get(row.marker)
        if item is None:
            unknown_rows.append(row.marker)
            continue
        row_markers.append(row.marker)
        finding = normalize_text(row.finding or "").strip()
        if finding:
            findings.setdefault(item.chunk.article_id, []).append(finding)

    direct_answer = normalize_text(draft.direct_answer.strip())
    synthesis = normalize_text(draft.synthesis.strip())
    text_markers = marker_ids(direct_answer) + marker_ids(synthesis)
    cited = sorted({m for m in [*text_markers, *row_markers] if m in known}, key=marker_number)
    cited_items = [known[m] for m in cited]

    return Answer(
        direct_answer=direct_answer,
        synthesis=synthesis,
        evidence_table=_rows(findings, cited_items),
        rating=_capped_rating(draft, cited_items),
        citations=[_citation(item) for item in cited_items],
        evidence_gap=evidence_gap,
        requested_evidence=list(requested),
        requested_label=requested_evidence_label(requested),
        unknown_row_markers=unknown_rows,
        prompt=PROMPT.id,
        model=model,
    )


def _rows(findings: dict[int, list[str]], cited_items: Sequence[ContextItem]) -> list[EvidenceRow]:
    """One row per cited article, in order of its first marker."""
    by_article: dict[int, list[ContextItem]] = {}
    for item in cited_items:
        by_article.setdefault(item.chunk.article_id, []).append(item)
    rows: list[EvidenceRow] = []
    for article_id, items in by_article.items():
        first, chunk = items[0], items[0].chunk
        texts = findings.get(article_id, [])
        rows.append(
            EvidenceRow(
                pmid=chunk.pmid,
                title=chunk.title,
                design=first.passage.grade.design,
                n=chunk.study_meta.sample_size,
                finding="; ".join(dict.fromkeys(texts)) or None,
                year=chunk.pub_date.year if chunk.pub_date else None,
                badge=first.passage.grade.badge,
                markers=[item.marker for item in items],
            )
        )
    return rows


def _capped_rating(draft: GeneratorDraft, cited_items: Sequence[ContextItem]) -> AnswerRating:
    ceiling = rating_ceiling(cited_items).rating
    level = draft.rating.level
    clamped = RATINGS.index(level) < RATINGS.index(ceiling)
    return AnswerRating(
        level=ceiling if clamped else level,
        justification=normalize_text(draft.rating.justification.strip()),
        ceiling=ceiling,
        clamped=clamped,
    )


def _citation(item: ContextItem) -> Citation:
    chunk = item.chunk
    return Citation(
        marker=item.marker,
        pmid=chunk.pmid,
        section=chunk.section,
        chunk_id=chunk.chunk_id,
        snippet=item.text,
    )
