from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.config import Settings
from app.errors import RateLimitedError
from app.generation.answer import Answer, GeneratorDraft, Issue
from app.generation.context import EvidenceContext, build_context
from app.generation.generate import assemble_answer
from app.generation.verify import (
    Judge,
    Verifier,
    answer_claims,
    check_answer,
    render_judge_request,
    user_warnings,
)
from app.models.llm import LLMClient
from tests.generation.builders import QUESTION, creatine_passages, stored, stored_data
from tests.llm_fakes import Reply, ScriptedProvider

CONFLICT = stored_data("generate_conflict")


@pytest.fixture
def context() -> EvidenceContext:
    return build_context(creatine_passages())


def answer_from(context: EvidenceContext, *, evidence_gap: bool = False, **changes: Any) -> Answer:
    draft = GeneratorDraft.model_validate({**stored_data("generate_conflict"), **changes})
    return assemble_answer(
        draft, context, evidence_gap=evidence_gap, requested=["meta_analysis"], model="m"
    )


def kinds(issues: list[Issue]) -> list[str]:
    return [i.kind for i in issues]


def test_valid_answer_passes_deterministic_checks(context: EvidenceContext) -> None:
    assert check_answer(answer_from(context), context) == []


def test_citing_a_nonexistent_id_fails(context: EvidenceContext) -> None:
    answer = answer_from(context, synthesis="Grip strength increased by 2.1 kg [C9].")
    issues = check_answer(answer, context)
    assert "unknown_citation" in kinds(issues)
    assert all(i.severity == "error" for i in issues if i.kind == "unknown_citation")


def test_unknown_row_marker_fails(context: EvidenceContext) -> None:
    rows = [{"marker": "C7", "finding": "Invented"}]
    issues = check_answer(answer_from(context, evidence_rows=rows), context)
    assert "unknown_citation" in kinds(issues)


def test_uncited_numeric_sentence_fails(context: EvidenceContext) -> None:
    synthesis = CONFLICT["synthesis"] + "\n\nGrip strength rose by 2.1 kg in the trial."
    issues = check_answer(answer_from(context, synthesis=synthesis), context)
    assert kinds(issues) == ["uncited_sentence"]
    assert "2.1 kg" in issues[0].message


def test_uncited_claim_without_numbers_fails(context: EvidenceContext) -> None:
    issues = check_answer(answer_from(context, direct_answer="Creatine works."), context)
    assert kinds(issues) == ["uncited_sentence"]


def test_number_missing_from_cited_passage_fails(context: EvidenceContext) -> None:
    synthesis = "Grip strength increased by 3.5 kg with creatine [C1]."
    issues = check_answer(answer_from(context, synthesis=synthesis), context)
    assert kinds(issues) == ["number_not_in_source"]
    assert "3.5" in issues[0].message


def test_number_from_another_passage_fails(context: EvidenceContext) -> None:
    synthesis = "The cohort found a difference of 2.1 kg [C3]."
    issues = check_answer(answer_from(context, synthesis=synthesis), context)
    assert kinds(issues) == ["number_not_in_source"]


def test_sample_size_and_year_count_as_sources(context: EvidenceContext) -> None:
    synthesis = "A 2023 cohort of 310 adults found no association [C3]."
    assert check_answer(answer_from(context, synthesis=synthesis), context) == []


def test_gap_answer_must_open_with_the_gap(context: EvidenceContext) -> None:
    gap = stored_data("generate_gap")
    stated = answer_from(context, evidence_gap=True, **gap)
    assert check_answer(stated, context) == []

    missing = answer_from(
        context, evidence_gap=True, direct_answer="Creatine may help grip strength [C1]."
    )
    assert kinds(check_answer(missing, context)) == ["gap_not_stated"]


def test_gap_statement_must_come_first(context: EvidenceContext) -> None:
    late = answer_from(
        context,
        evidence_gap=True,
        direct_answer="Results are mixed [C1, C3]. No meta-analysis has examined this.",
    )
    assert kinds(check_answer(late, context)) == ["gap_not_stated"]


def test_gap_restated_in_synthesis_needs_no_citation(context: EvidenceContext) -> None:
    gap = stored_data("generate_gap")
    synthesis = "No systematic review has compared these directly. " + gap["synthesis"]
    answer = answer_from(context, evidence_gap=True, **{**gap, "synthesis": synthesis})
    assert check_answer(answer, context) == []


def test_interpretive_sentences_still_need_citations(context: EvidenceContext) -> None:
    gap = stored_data("generate_gap")
    synthesis = gap["synthesis"] + " Because these trials differ, they cannot settle the question."
    answer = answer_from(context, evidence_gap=True, **{**gap, "synthesis": synthesis})
    [problem] = check_answer(answer, context)
    assert problem.kind == "uncited_sentence"
    assert "end it with the markers" in problem.message


def test_clamped_rating_is_a_warning(context: EvidenceContext) -> None:
    answer = answer_from(context, rating={"level": "high", "justification": "x"})
    [warning] = check_answer(answer, context)
    assert (warning.kind, warning.severity) == ("rating_clamped", "warning")


def test_claims_cover_sentences_and_rows(context: EvidenceContext) -> None:
    claims = answer_claims(answer_from(context))
    assert [c.id for c in claims] == [f"S{i}" for i in range(1, 8)]
    assert [c.location for c in claims][:4] == ["direct_answer", *["synthesis"] * 3]
    assert claims[4].location == "evidence_table PMID 90000001"
    assert claims[0].markers == ("C1", "C3")


def test_judge_request_lists_claims_and_passages(context: EvidenceContext) -> None:
    answer = answer_from(context, evidence_gap=True, **stored_data("generate_gap"))
    text = render_judge_request(QUESTION, answer, context, answer_claims(answer))
    assert "EVIDENCE GAP: yes; requested evidence: meta-analysis or systematic review" in text
    assert "FIRST SENTENCE: No systematic review or meta-analysis has examined" in text
    assert "S1 [C1, C3] The closest evidence, from primary studies, is mixed ." in text
    assert text.endswith(context.text)


BuildVerifier = Callable[[list[Reply]], tuple[Verifier, ScriptedProvider]]


@pytest.fixture
def verifier(make_settings: Callable[..., Settings]) -> BuildVerifier:
    def _build(replies: list[Reply]) -> tuple[Verifier, ScriptedProvider]:
        provider = ScriptedProvider(replies)
        return Verifier(Judge(LLMClient(make_settings(), provider))), provider

    return _build


async def test_supported_answer_passes(verifier: BuildVerifier, context: EvidenceContext) -> None:
    built, provider = verifier([stored("judge_supported")])
    result = await built.verify(QUESTION, answer_from(context), context)
    assert result.passed
    assert result.judge_status == "ok"
    assert result.judge_prompt == "judge@v1"
    assert "CLAIMS:" in provider.prompts[0]


async def test_unsupported_claim_fails(verifier: BuildVerifier, context: EvidenceContext) -> None:
    claims = stored_data("judge_supported")["claims"]
    claims[1] = {"id": "S2", "verdict": "unsupported", "note": "C1 does not report this"}
    built, _ = verifier([stored("judge_supported", claims=claims)])
    result = await built.verify(QUESTION, answer_from(context), context)
    assert not result.passed
    [error] = result.errors
    assert (error.kind, error.claim_id, error.location) == ("unsupported_claim", "S2", "synthesis")
    assert "C1 does not report this" in error.message


async def test_partial_support_is_a_warning(
    verifier: BuildVerifier, context: EvidenceContext
) -> None:
    claims = [{"id": "S3", "verdict": "Partial", "note": "population differs"}]
    built, _ = verifier([stored("judge_supported", claims=claims)])
    result = await built.verify(QUESTION, answer_from(context), context)
    assert result.passed
    assert kinds(result.warnings) == ["partially_supported"]


async def test_judge_flags_an_omitted_conflict(
    verifier: BuildVerifier, context: EvidenceContext
) -> None:
    built, _ = verifier(
        [stored("judge_supported", conflict_omitted=True, conflict_note="C1 vs C3")]
    )
    result = await built.verify(QUESTION, answer_from(context), context)
    assert kinds(result.errors) == ["conflict_omitted"]


async def test_judge_rejects_a_gap_statement(
    verifier: BuildVerifier, context: EvidenceContext
) -> None:
    answer = answer_from(context, evidence_gap=True, **stored_data("generate_gap"))
    built, _ = verifier([stored("judge_supported", gap_statement_ok=False)])
    result = await built.verify(QUESTION, answer, context)
    assert kinds(result.errors) == ["gap_not_stated"]


async def test_gap_failure_is_reported_once(
    verifier: BuildVerifier, context: EvidenceContext
) -> None:
    answer = answer_from(context, evidence_gap=True, direct_answer="Creatine may help [C1].")
    built, _ = verifier([stored("judge_supported", gap_statement_ok=False)])
    result = await built.verify(QUESTION, answer, context)
    assert kinds(result.errors) == ["gap_not_stated"]


@pytest.mark.parametrize("failure", [RateLimitedError("slow down"), "not json"])
async def test_judge_fails_open(
    verifier: BuildVerifier, context: EvidenceContext, failure: Reply
) -> None:
    replies: list[Reply] = [failure] if isinstance(failure, Exception) else [failure, failure]
    built, _ = verifier(replies)
    result = await built.verify(QUESTION, answer_from(context), context)
    assert result.passed
    assert result.judge_status == "failed"
    assert kinds(result.warnings) == ["judge_unavailable"]
    assert user_warnings(result) == ["Automatic fact-checking was unavailable for this answer."]


async def test_judge_can_be_skipped_after_deterministic_errors(
    verifier: BuildVerifier, context: EvidenceContext
) -> None:
    built, provider = verifier([])
    answer = answer_from(context, synthesis="Invented [C9].")
    result = await built.verify(QUESTION, answer, context, judge_on_errors=False)
    assert result.judge_status == "skipped"
    assert provider.prompts == []
    assert not result.passed


async def test_without_a_judge_only_rules_run(context: EvidenceContext) -> None:
    result = await Verifier(None).verify(QUESTION, answer_from(context), context)
    assert result.passed
    assert result.judge_status == "skipped"
