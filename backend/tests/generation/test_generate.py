from __future__ import annotations

from collections.abc import Callable

import pytest

from app.config import Settings
from app.errors import LLMOutputError
from app.generation.context import build_context
from app.generation.generate import Generator, render_request
from app.generation.plan import Plan
from app.models.llm import LLMClient
from tests.generation.builders import QUESTION, creatine_passages, stored, stored_data
from tests.llm_fakes import Reply, ScriptedProvider

BuildGenerator = Callable[[list[Reply]], tuple[Generator, ScriptedProvider]]


@pytest.fixture
def generator(make_settings: Callable[..., Settings]) -> BuildGenerator:
    def _build(replies: list[Reply]) -> tuple[Generator, ScriptedProvider]:
        provider = ScriptedProvider(replies)
        return Generator(LLMClient(make_settings(), provider)), provider

    return _build


async def test_code_fills_the_evidence_table(generator: BuildGenerator) -> None:
    built, _ = generator([stored("generate_conflict")])
    answer = await built.generate(QUESTION, build_context(creatine_passages()), evidence_gap=False)
    trial, cohort, review = answer.evidence_table
    assert (trial.pmid, trial.design, trial.n, trial.year, trial.badge) == (
        90_000_001,
        "rct",
        40,
        2024,
        "RCT",
    )
    assert trial.title == "Synthetic study 1"
    assert trial.markers == ["C1"]
    assert cohort.badge == "Observational"
    assert cohort.n == 310
    assert review.finding == "Higher upper-body strength across nine trials in all ages"
    assert answer.prompt == "generate@v2"


async def test_citations_resolve_markers_to_passages(generator: BuildGenerator) -> None:
    built, _ = generator([stored("generate_conflict")])
    context = build_context(creatine_passages())
    answer = await built.generate(QUESTION, context, evidence_gap=False)
    assert [(c.marker, c.pmid, c.section, c.chunk_id) for c in answer.citations] == [
        ("C1", 90_000_001, "Abstract-RESULTS", 11),
        ("C3", 90_000_002, "Abstract-RESULTS", 21),
        ("C4", 90_000_003, "Abstract-RESULTS", 31),
    ]
    assert answer.citations[0].snippet == context.by_marker["C1"].text


async def test_cited_article_without_a_row_gets_one(generator: BuildGenerator) -> None:
    data = stored_data("generate_conflict")
    built, _ = generator([stored("generate_conflict", evidence_rows=data["evidence_rows"][:1])])
    answer = await built.generate(QUESTION, build_context(creatine_passages()), evidence_gap=False)
    assert [row.pmid for row in answer.evidence_table] == [90_000_001, 90_000_002, 90_000_003]
    assert answer.evidence_table[1].finding is None


async def test_rows_for_unknown_markers_are_recorded(generator: BuildGenerator) -> None:
    rows = [{"marker": "[C9]", "finding": "Invented"}]
    built, _ = generator([stored("generate_conflict", evidence_rows=rows)])
    answer = await built.generate(QUESTION, build_context(creatine_passages()), evidence_gap=False)
    assert answer.unknown_row_markers == ["C9"]
    assert len(answer.evidence_table) == 3


async def test_rating_is_capped_at_the_ceiling(generator: BuildGenerator) -> None:
    rating = {"level": "High", "justification": "Strong."}
    built, _ = generator([stored("generate_conflict", rating=rating)])
    answer = await built.generate(QUESTION, build_context(creatine_passages()), evidence_gap=False)
    assert answer.rating.level == "low"
    assert answer.rating.ceiling == "low"
    assert answer.rating.clamped is True


async def test_a_lower_rating_is_kept(generator: BuildGenerator) -> None:
    rating = {"level": "very low", "justification": "Inconsistent."}
    built, _ = generator([stored("generate_conflict", rating=rating)])
    answer = await built.generate(QUESTION, build_context(creatine_passages()), evidence_gap=False)
    assert answer.rating.level == "very_low"
    assert answer.rating.clamped is False


async def test_typographic_hyphens_and_spaces_are_normalized(generator: BuildGenerator) -> None:
    direct = "No meta\u2011analysis was found.\u202fThe evidence is mixed [C1,\u00a0C3]."
    built, _ = generator([stored("generate_conflict", direct_answer=direct)])
    answer = await built.generate(QUESTION, build_context(creatine_passages()), evidence_gap=True)
    assert answer.direct_answer == "No meta-analysis was found. The evidence is mixed [C1, C3]."


async def test_invalid_output_fails_closed(generator: BuildGenerator) -> None:
    built, _ = generator(["not json", "still not json"])
    with pytest.raises(LLMOutputError):
        await built.generate(QUESTION, build_context(creatine_passages()), evidence_gap=False)


def test_request_carries_gap_ceiling_framing_and_passages() -> None:
    plan = Plan(
        mode="question",
        intervention="creatine",
        outcome="grip strength",
        outcome_class="clinical",
        requested_evidence=["meta_analysis"],
        standalone_query=QUESTION,
    )
    context = build_context(creatine_passages())
    text = render_request(QUESTION, context, plan=plan, evidence_gap=True)
    assert "EVIDENCE GAP: yes" in text
    assert "no meta-analysis or systematic review addressing this question" in text
    assert "RATING CEILING: low (" in text
    assert "intervention: creatine; outcome: grip strength; outcome class: clinical" in text
    assert text.endswith(context.text)
