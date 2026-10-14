"""Answer a question from retrieved passages: generate, verify, regenerate once, then warn.

M7's graph runs ``generate`` and ``verify`` as separate nodes with the same retry rule; this
service is the same loop for scripts and tests.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.errors import AppError
from app.generation.answer import Answer, AnswerRating, Issue, VerificationResult, issue
from app.generation.context import ContextBudget, EvidenceContext, build_context
from app.generation.gap import gap_sentence, requested_evidence_label
from app.generation.plan import Plan
from app.generation.verify import user_warnings
from app.models.llm import UsageMeter
from app.retrieval.retriever import EvidencePassage

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class AnsweredQuestion:
    answer: Answer
    verification: VerificationResult
    context: EvidenceContext
    warnings: tuple[str, ...]


class AnswerService:
    def __init__(
        self,
        generator: AnswerGenerator,
        verifier: AnswerVerifier,
        budget: ContextBudget | None = None,
    ) -> None:
        self._generator = generator
        self._verifier = verifier
        self._budget = budget or ContextBudget()

    async def answer(
        self,
        question: str,
        passages: Sequence[EvidencePassage],
        *,
        evidence_gap: bool,
        plan: Plan | None = None,
        meter: UsageMeter | None = None,
    ) -> AnsweredQuestion:
        """The generator fails closed (its errors propagate on the first attempt); a failed
        retry returns the first answer with a warning.
        """
        context = build_context(passages, self._budget)
        if not context.items:
            return AnsweredQuestion(*no_evidence(plan), context, ())

        first = await self._generator.generate(
            question, context, plan=plan, evidence_gap=evidence_gap, meter=meter
        )
        checked = await self._verifier.verify(
            question, first, context, attempt=1, judge_on_errors=False, meter=meter
        )
        if checked.passed:
            return _done(first, checked, context)

        try:
            second = await self._generator.generate(
                question,
                context,
                plan=plan,
                evidence_gap=evidence_gap,
                feedback=checked.errors,
                meter=meter,
            )
        except AppError as exc:
            logger.warning("regeneration_failed", extra={"reason": exc.message})
            note = issue("regeneration_failed", "answer", f"retry failed: {exc.message}")
            return _done(*with_gap_fallback(first, with_issue(checked, note)), context)

        rechecked = await self._verifier.verify(
            question, second, context, attempt=2, judge_on_errors=True, meter=meter
        )
        return _done(*with_gap_fallback(second, rechecked), context)


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


def _done(answer: Answer, result: VerificationResult, context: EvidenceContext) -> AnsweredQuestion:
    return AnsweredQuestion(answer, result, context, tuple(user_warnings(result)))


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
