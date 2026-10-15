"""The answer step's interfaces, and the rules the pipeline applies around it.

The generate and verify nodes in ``app.graph.nodes`` run the loop: generate, verify, and
regenerate once with the failed checks as feedback. These helpers are the pieces of that loop
that do not need a model: the answer when there are no passages, the gap fallback, and
adding an issue to a verification result.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.generation.answer import Answer, AnswerRating, Issue, VerificationResult, issue
from app.generation.context import EvidenceContext
from app.generation.gap import gap_sentence, requested_evidence_label
from app.generation.plan import Plan
from app.models.llm import UsageMeter


class AnswerGenerator(Protocol):
    async def generate(
        self,
        question: str,
        context: EvidenceContext,
        *,
        plan: Plan | None = None,
        evidence_gap: bool,
        feedback: Sequence[Issue] = (),
        meter: UsageMeter | None = None,
    ) -> Answer: ...


class AnswerVerifier(Protocol):
    async def verify(
        self,
        question: str,
        answer: Answer,
        context: EvidenceContext,
        *,
        attempt: int = 1,
        judge_on_errors: bool = True,
        meter: UsageMeter | None = None,
    ) -> VerificationResult: ...


def with_gap_fallback(
    answer: Answer, result: VerificationResult
) -> tuple[Answer, VerificationResult]:
    """If the gap was never stated, code states it: the gap must not be lost."""
    if not any(i.kind == "gap_not_stated" for i in result.issues):
        return answer, result
    fixed = answer.model_copy(
        update={"direct_answer": f"{gap_sentence(answer.requested_label)} {answer.direct_answer}"}
    )
    issues = [i for i in result.issues if i.kind != "gap_not_stated"]
    issues.append(
        issue("gap_statement_inserted", "direct_answer", "the gap statement was added by code")
    )
    passed = not any(i.severity == "error" for i in issues)
    return fixed, result.model_copy(update={"issues": issues, "passed": passed})


def with_issue(result: VerificationResult, extra: Issue) -> VerificationResult:
    issues = [*result.issues, extra]
    passed = not any(i.severity == "error" for i in issues)
    return result.model_copy(update={"issues": issues, "passed": passed})


def no_evidence(plan: Plan | None) -> tuple[Answer, VerificationResult]:
    """No passages at all: say so without calling a model."""
    requested = list(plan.requested_evidence) if plan else []
    label = requested_evidence_label(requested)
    answer = Answer(
        direct_answer=f"{gap_sentence(label)} No other relevant PubMed studies were found.",
        synthesis="",
        rating=AnswerRating(
            level="very_low",
            justification="No studies were available to assess.",
            ceiling="very_low",
        ),
        evidence_gap=True,
        requested_evidence=requested,
        requested_label=label,
    )
    return answer, VerificationResult(passed=True, judge_status="skipped", attempts=0)
