from __future__ import annotations

from collections.abc import Callable

import pytest

from app.config import Settings
from app.errors import RateLimitedError
from app.generation.context import ContextBudget
from app.generation.factory import build_answer_service, context_budget
from app.generation.service import AnswerService
from app.models.llm import LLMClient, UsageMeter
from tests.generation.builders import QUESTION, creatine_passages, stored
from tests.llm_fakes import Reply, ScriptedProvider

GAP_SENTENCE = "No systematic review or meta-analysis addressing this question was found in PubMed."
BuildService = Callable[[list[Reply]], tuple[AnswerService, ScriptedProvider]]


@pytest.fixture
def service(make_settings: Callable[..., Settings]) -> BuildService:
    def _build(replies: list[Reply]) -> tuple[AnswerService, ScriptedProvider]:
        provider = ScriptedProvider(replies)
        settings = make_settings()
        return build_answer_service(settings, LLMClient(settings, provider)), provider

    return _build


async def test_conflicting_evidence_answer_reports_both_directions(
    service: BuildService,
) -> None:
    built, provider = service([stored("generate_conflict"), stored("judge_supported")])
    meter = UsageMeter()
    result = await built.answer(QUESTION, creatine_passages(), evidence_gap=False, meter=meter)
    assert result.verification.passed
    assert result.verification.attempts == 1
    synthesis = result.answer.synthesis
    assert "increased by 2.1 kg" in synthesis
    assert "no association" in synthesis
    assert {"C1", "C3"} <= {c.marker for c in result.answer.citations}
    assert result.warnings == ()
    assert len(meter.calls) == 2
    assert provider.systems[0].startswith("You write evidence summaries")
    assert provider.systems[1].startswith("You check an evidence summary")


async def test_omitted_conflict_triggers_one_regeneration(service: BuildService) -> None:
    one_sided = "A small randomized trial found that grip strength increased by 2.1 kg [C1]."
    built, provider = service(
        [
            stored("generate_conflict", synthesis=one_sided),
            stored("judge_supported", conflict_omitted=True, conflict_note="C1 vs C3"),
            stored("generate_conflict"),
            stored("judge_supported"),
        ]
    )
    result = await built.answer(QUESTION, creatine_passages(), evidence_gap=False)
    assert result.verification.passed
    assert result.verification.attempts == 2
    assert "YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS" in provider.prompts[2]
    assert "C1 vs C3" in provider.prompts[2]
    assert "no association" in result.answer.synthesis


async def test_deterministic_failure_skips_the_first_judge_call(service: BuildService) -> None:
    built, provider = service(
        [
            stored("generate_conflict", synthesis="Grip strength rose by 2.1 kg [C9]."),
            stored("generate_conflict"),
            stored("judge_supported"),
        ]
    )
    result = await built.answer(QUESTION, creatine_passages(), evidence_gap=False)
    assert result.verification.passed
    assert result.verification.attempts == 2
    assert len(provider.prompts) == 3
    assert "cites C9" in provider.prompts[1]


async def test_persistent_failure_returns_with_a_warning(service: BuildService) -> None:
    bad = stored("generate_conflict", synthesis="Grip strength rose by 9.9 kg [C1].")
    built, _ = service([bad, bad, stored("judge_supported")])
    result = await built.answer(QUESTION, creatine_passages(), evidence_gap=False)
    assert not result.verification.passed
    assert [i.kind for i in result.verification.errors] == ["number_not_in_source"]
    assert result.warnings == (
        "Some statements in this answer could not be verified against the cited passages.",
    )


async def test_gap_answer_opens_with_the_gap(service: BuildService) -> None:
    gap_judge = stored("judge_supported", gap_statement_ok=True)
    built, provider = service([stored("generate_gap"), gap_judge])
    result = await built.answer(QUESTION, creatine_passages(), evidence_gap=True)
    assert result.verification.passed
    assert result.answer.direct_answer.startswith("No systematic review or meta-analysis")
    assert "EVIDENCE GAP: yes" in provider.prompts[0]
    assert "EVIDENCE GAP: yes" in provider.prompts[1]


async def test_code_states_the_gap_when_the_model_never_does(service: BuildService) -> None:
    silent = stored("generate_gap", direct_answer="Creatine may help grip strength [C1].")
    built, _ = service([silent, silent, stored("judge_supported", gap_statement_ok=False)])
    result = await built.answer(QUESTION, creatine_passages(), evidence_gap=True)
    assert result.answer.direct_answer.startswith(GAP_SENTENCE)
    assert result.verification.passed
    assert [i.kind for i in result.verification.warnings] == ["gap_statement_inserted"]


async def test_failed_regeneration_keeps_the_first_answer(service: BuildService) -> None:
    first = stored("generate_conflict", synthesis="Grip strength rose by 2.1 kg [C9].")
    built, _ = service([first, RateLimitedError("slow down")])
    result = await built.answer(QUESTION, creatine_passages(), evidence_gap=False)
    assert result.answer.synthesis == "Grip strength rose by 2.1 kg [C9]."
    kinds = [i.kind for i in result.verification.issues]
    assert kinds == ["unknown_citation", "regeneration_failed"]
    assert not result.verification.passed


async def test_first_generation_failure_propagates(service: BuildService) -> None:
    built, _ = service([RateLimitedError("slow down")])
    with pytest.raises(RateLimitedError):
        await built.answer(QUESTION, creatine_passages(), evidence_gap=False)


async def test_no_passages_needs_no_model(service: BuildService) -> None:
    built, provider = service([])
    result = await built.answer(QUESTION, [], evidence_gap=True)
    assert result.answer.direct_answer.startswith(GAP_SENTENCE)
    assert result.answer.rating.level == "very_low"
    assert provider.prompts == []


def test_budget_comes_from_settings(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(
        generation_context_tokens=900,
        passage_max_chars=500,
        context_min_chunks=2,
        context_max_chunks=5,
    )
    assert context_budget(settings) == ContextBudget(
        max_tokens=900, passage_max_chars=500, min_chunks=2, max_chunks=5
    )
