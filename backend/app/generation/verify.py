"""Verify an answer against its context.

Deterministic checks decide what a rule can: every marker exists, every factual sentence is
cited, every number in a cited sentence appears in its passages, and a required gap statement
opens the answer. The judge (a different model family from the generator) decides what needs
reading: whether each cited claim is supported, and whether conflicting findings were left out.

The judge fails open: if it is rate limited or returns unusable output, the deterministic
checks still gate the answer and a warning says it was not independently checked.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.errors import AppError
from app.generation.answer import (
    Answer,
    Issue,
    IssueKind,
    JudgeStatus,
    JudgeVerdict,
    VerificationResult,
    issue,
)
from app.generation.citations import (
    checkable_numbers,
    marker_ids,
    numbers_in,
    sentences,
    strip_markers,
)
from app.generation.context import ELLIPSIS, ContextItem, EvidenceContext
from app.generation.gap import states_gap
from app.generation.prompts import load_prompt
from app.models.llm import ChatMessage, LLMClient, LLMRole, UsageMeter

logger = logging.getLogger(__name__)

JUDGE_PROMPT = load_prompt("judge", "v1")
CLIP_CHARS = 140


@dataclass(frozen=True, slots=True)
class Claim:
    id: str
    location: str
    text: str
    markers: tuple[str, ...]


def answer_claims(answer: Answer) -> list[Claim]:
    """Cited sentences of the direct answer and synthesis, then each row's finding."""
    claims: list[Claim] = []
    for location, text in _sections(answer):
        for sentence in sentences(text):
            if markers := marker_ids(sentence):
                claims.append(Claim(f"S{len(claims) + 1}", location, sentence, tuple(markers)))
    for row in answer.evidence_table:
        if row.finding:
            claims.append(
                Claim(
                    f"S{len(claims) + 1}",
                    f"evidence_table PMID {row.pmid}",
                    row.finding,
                    tuple(row.markers),
                )
            )
    return claims


def first_sentence(text: str) -> str:
    found = sentences(text)
    return found[0] if found else ""


def check_answer(answer: Answer, context: EvidenceContext) -> list[Issue]:
    known = context.by_marker
    issues: list[Issue] = []
    for location, text in _sections(answer):
        for sentence in sentences(text):
            markers = marker_ids(sentence)
            unknown = [m for m in markers if m not in known]
            if unknown:
                issues.append(
                    issue(
                        "unknown_citation",
                        location,
                        f"cites {', '.join(unknown)}, which is not among the passages: "
                        f"{_clip(sentence)}",
                    )
                )
            # A statement that evidence is absent has nothing to cite.
            exempt = answer.evidence_gap and states_gap(sentence, answer.requested_evidence)
            if not markers and not exempt:
                issues.append(
                    issue(
                        "uncited_sentence",
                        location,
                        "no citation; end it with the markers of the passages it is about, "
                        f"or remove it: {_clip(sentence)}",
                    )
                )
    for marker in answer.unknown_row_markers:
        issues.append(
            issue("unknown_citation", "evidence_table", f"row cites {marker}, not a passage")
        )
    for claim in answer_claims(answer):
        cited = [known[m] for m in claim.markers if m in known]
        if not cited:
            continue
        missing = checkable_numbers(claim.text) - source_numbers(cited)
        if missing:
            shown = ", ".join(sorted(format(n, "f") for n in missing))
            issues.append(
                issue(
                    "number_not_in_source",
                    claim.location,
                    f"{shown} not found in {', '.join(claim.markers)}: {_clip(claim.text)}",
                )
            )
    if answer.evidence_gap and not states_gap(
        first_sentence(answer.direct_answer), answer.requested_evidence
    ):
        issues.append(
            issue(
                "gap_not_stated",
                "direct_answer",
                f"the first sentence must say that no {answer.requested_label} addressing the "
                "question was found",
            )
        )
    if answer.rating.clamped:
        issues.append(
            issue(
                "rating_clamped",
                "rating",
                f"rating lowered to {answer.rating.ceiling}, the most the cited studies allow",
            )
        )
    return issues


def source_numbers(items: Sequence[ContextItem]) -> set[Decimal]:
    """Numbers in the passages as shown, plus each article's sample size and year."""
    values: set[Decimal] = set()
    for item in items:
        values |= numbers_in(item.text)
        chunk = item.chunk
        if chunk.study_meta.sample_size is not None:
            values.add(Decimal(chunk.study_meta.sample_size))
        if chunk.pub_date is not None:
            values.add(Decimal(chunk.pub_date.year))
    return values


class Judge:
    def __init__(self, llm: LLMClient, *, max_tokens: int = 1500) -> None:
        self._llm = llm
        self._max_tokens = max_tokens

    async def judge(
        self,
        question: str,
        answer: Answer,
        context: EvidenceContext,
        claims: Sequence[Claim],
        meter: UsageMeter | None = None,
    ) -> JudgeVerdict:
        messages = [
            ChatMessage("system", JUDGE_PROMPT.text),
            ChatMessage("user", render_judge_request(question, answer, context, claims)),
        ]
        return await self._llm.complete_json(
            LLMRole.JUDGE, messages, JudgeVerdict, max_tokens=self._max_tokens, meter=meter
        )


def render_judge_request(
    question: str, answer: Answer, context: EvidenceContext, claims: Sequence[Claim]
) -> str:
    lines = [f"QUESTION: {question}"]
    if answer.evidence_gap:
        lines.append(f"EVIDENCE GAP: yes; requested evidence: {answer.requested_label}")
        lines.append(f"FIRST SENTENCE: {first_sentence(answer.direct_answer)}")
    else:
        lines.append("EVIDENCE GAP: no")
    lines.append("CLAIMS:")
    lines += [f"{c.id} [{', '.join(c.markers)}] {strip_markers(c.text)}" for c in claims]
    lines += ["", "PASSAGES:", context.text]
    return "\n".join(lines)


def judge_issues(verdict: JudgeVerdict, claims: Sequence[Claim], answer: Answer) -> list[Issue]:
    by_id = {claim.id: claim for claim in claims}
    issues: list[Issue] = []
    for item in verdict.claims:
        claim = by_id.get(item.id)
        if claim is None or item.verdict == "supported":
            continue
        reason = item.note or f"not supported by {', '.join(claim.markers)}"
        kind: IssueKind = (
            "unsupported_claim" if item.verdict == "unsupported" else "partially_supported"
        )
        issues.append(
            issue(kind, claim.location, f"{reason}: {_clip(claim.text)}", claim_id=claim.id)
        )
    if answer.evidence_gap and verdict.gap_statement_ok is False:
        issues.append(
            issue(
                "gap_not_stated",
                "direct_answer",
                f"the judge found the first sentence does not state that no "
                f"{answer.requested_label} was found",
            )
        )
    if verdict.conflict_omitted:
        note = verdict.conflict_note or "passages disagree but only one direction is reported"
        message = f"report both directions of the evidence: {note}"
        issues.append(issue("conflict_omitted", "synthesis", message))
    return issues


class Verifier:
    def __init__(self, judge: Judge | None) -> None:
        """Without a judge, only the deterministic checks run."""
        self._judge = judge

    async def verify(
        self,
        question: str,
        answer: Answer,
        context: EvidenceContext,
        *,
        attempt: int = 1,
        judge_on_errors: bool = True,
        meter: UsageMeter | None = None,
    ) -> VerificationResult:
        """With ``judge_on_errors=False`` the judge is skipped when a deterministic check has
        already failed, saving its daily token budget for the regenerated answer.
        """
        issues = check_answer(answer, context)
        has_errors = any(i.severity == "error" for i in issues)
        status: JudgeStatus = "skipped"
        claims = answer_claims(answer)
        judge = self._judge
        has_work = bool(claims) or answer.evidence_gap
        if judge is not None and has_work and (judge_on_errors or not has_errors):
            try:
                verdict = await judge.judge(question, answer, context, claims, meter)
            except AppError as exc:
                logger.warning("judge_failed", extra={"reason": exc.message})
                status = "failed"
                issues.append(
                    issue("judge_unavailable", "answer", f"the judge could not run: {exc.message}")
                )
            else:
                status = "ok"
                issues += _without_duplicates(judge_issues(verdict, claims, answer), issues)
        return VerificationResult(
            passed=not any(i.severity == "error" for i in issues),
            issues=issues,
            judge_status=status,
            attempts=attempt,
            judge_prompt=JUDGE_PROMPT.id if status != "skipped" else None,
        )


def user_warnings(result: VerificationResult) -> list[str]:
    """Messages for the UI's warning event."""
    messages: list[str] = []
    if not result.passed:
        messages.append(
            "Some statements in this answer could not be verified against the cited passages."
        )
    if result.judge_status == "failed":
        messages.append("Automatic fact-checking was unavailable for this answer.")
    return messages


def _without_duplicates(new: list[Issue], existing: list[Issue]) -> list[Issue]:
    seen = {i.kind for i in existing if i.kind == "gap_not_stated"}
    return [i for i in new if i.kind not in seen]


def _sections(answer: Answer) -> list[tuple[str, str]]:
    return [("direct_answer", answer.direct_answer), ("synthesis", answer.synthesis)]


def _clip(text: str) -> str:
    text = strip_markers(text)
    return text if len(text) <= CLIP_CHARS else f"{text[:CLIP_CHARS].rstrip()}{ELLIPSIS}"
