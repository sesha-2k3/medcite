from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import RateLimitedError, UpstreamError
from app.generation.plan import (
    EVIDENCE_FILTERS,
    DateWindow,
    Plan,
    Planner,
    fallback_plan,
)
from app.generation.pubmed_query import RETRACTED_EXCLUSION, concept_clause, validate_query
from app.models.llm import LLMClient
from tests.generation.fakes import FakeSearcher
from tests.llm_fakes import Reply, ScriptedProvider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "llm"
TODAY = date(2026, 10, 14)
DUMBBELL = "Is there a meta-analysis of dumbbell curl vs barbell curl for biceps muscle growth?"


def stored(name: str, **changes: object) -> str:
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    data.update(changes)
    return json.dumps(data)


BuildPlanner = Callable[[list[Reply]], tuple[Planner, ScriptedProvider]]


@pytest.fixture
def planner(make_settings: Callable[..., Settings]) -> BuildPlanner:
    def _build(replies: list[Reply]) -> tuple[Planner, ScriptedProvider]:
        provider = ScriptedProvider(replies)
        return Planner(LLMClient(make_settings(), provider)), provider

    return _build


def assert_well_formed(plan: Plan) -> None:
    for tier in plan.tiers:
        assert validate_query(tier.pubmed_query) == [], tier.pubmed_query
        assert tier.pubmed_query.endswith(RETRACTED_EXCLUSION)


async def plan_for(planner: BuildPlanner, reply: Reply, question: str = DUMBBELL) -> Plan:
    built, _ = planner([reply])
    return await built.plan(question, today=TODAY)


async def test_dumbbell_question_gets_four_tiers(planner: BuildPlanner) -> None:
    plan = await plan_for(planner, stored("plan_dumbbell"))

    assert [t.level for t in plan.tiers] == [1, 2, 3, 4]
    assert [t.purpose for t in plan.tiers] == [
        "exact evidence",
        "direct primary studies",
        "related syntheses",
        "mechanistic or surrogate",
    ]
    assert (plan.mode, plan.outcome_class, plan.source) == ("question", "physiological", "llm")
    assert plan.requested_evidence == ["meta_analysis", "systematic_review"]
    assert plan.prompt == "plan@v3"
    assert_well_formed(plan)


async def test_tier_one_is_restricted_to_the_requested_syntheses(planner: BuildPlanner) -> None:
    tier1 = (await plan_for(planner, stored("plan_dumbbell"))).tiers[0].pubmed_query
    assert '("meta-analysis"[pt] OR "systematic review"[pt])' in tier1
    assert '"barbell curl*"[tiab]' in tier1
    assert '"randomized controlled trial"[pt]' not in tier1


@pytest.mark.parametrize("name", ["outcome", "broader_intervention", "surrogate_outcome"])
async def test_every_mesh_concept_is_searched_with_title_abstract_terms_too(
    planner: BuildPlanner, name: str
) -> None:
    plan = await plan_for(planner, stored("plan_dumbbell"))
    concept = getattr(plan.concepts, name)
    clause = concept_clause(concept.terms())
    assert clause is not None
    assert "[mh]" in clause
    assert "[tiab]" in clause
    assert any(clause in tier.pubmed_query for tier in plan.tiers)


async def test_mesh_headings_are_also_searched_as_text(planner: BuildPlanner) -> None:
    plan = await plan_for(planner, stored("plan_dumbbell"))
    tier1 = plan.tiers[0].pubmed_query
    assert '"Hypertrophy"[mh]' in tier1
    assert '"hypertrophy"[tiab]' in tier1
    assert '"skeletal muscle"[tiab]' in tier1


async def test_lower_tiers_broaden_the_search(planner: BuildPlanner) -> None:
    tiers = (await plan_for(planner, stored("plan_dumbbell"))).tiers
    direct, related, mechanistic = (t.pubmed_query for t in tiers[1:])
    assert 'NOT ("meta-analysis"[pt] OR "systematic review"[pt] OR "review"[pt]' in direct
    assert '"Resistance Training"[mh]' in related
    assert "barbell" not in related
    assert "biceps" not in related
    assert '"review"[pt]' in related
    assert '"Electromyography"[mh]' in mechanistic
    assert '"biceps"[tiab]' in mechanistic
    assert "barbell" not in mechanistic
    assert "[pt]" not in mechanistic.removesuffix(RETRACTED_EXCLUSION)


async def test_no_requested_type_means_best_available_evidence(planner: BuildPlanner) -> None:
    plan = await plan_for(planner, stored("plan_dumbbell", requested_evidence=[]))
    assert '"guideline"[pt]' in plan.tiers[0].pubmed_query


async def test_requested_rcts_filter_tier_one_to_rcts(planner: BuildPlanner) -> None:
    plan = await plan_for(planner, stored("plan_dumbbell", requested_evidence=["rct"]))
    assert f"({EVIDENCE_FILTERS['rct'][0]})" in plan.tiers[0].pubmed_query
    assert '"meta-analysis"[pt] OR' not in plan.tiers[0].pubmed_query


async def test_question_date_range_filters_publication_date(planner: BuildPlanner) -> None:
    window = {"from": "2020-01-01", "to": None}
    plan = await plan_for(planner, stored("plan_dumbbell", date_range=window))
    assert all("2020/01/01:3000[dp]" in t.pubmed_query for t in plan.tiers)


async def test_hybrid_mode_filters_entrez_date(planner: BuildPlanner) -> None:
    window = {"from": "2026-04-14", "to": None}
    plan = await plan_for(planner, stored("plan_dumbbell", mode="hybrid", date_range=window))
    assert len(plan.tiers) == 4
    assert all("2026/04/14:3000[edat]" in t.pubmed_query for t in plan.tiers)


async def test_discovery_gets_one_recent_research_tier(planner: BuildPlanner) -> None:
    question = "recent research on diabetes in older people"
    plan = await plan_for(planner, stored("plan_discovery"), question)
    [tier] = plan.tiers
    assert tier.purpose == "recent research"
    assert '"Aged"[mh]' in tier.pubmed_query
    assert '"Diabetes Mellitus"[mh]' in tier.pubmed_query
    assert "2026/04/14:3000[edat]" in tier.pubmed_query
    assert_well_formed(plan)


async def test_request_window_overrides_and_forces_discovery(planner: BuildPlanner) -> None:
    built, _ = planner([stored("plan_dumbbell")])
    window = DateWindow(start=date(2026, 7, 1), end=date(2026, 10, 1))
    plan = await built.plan("biceps curls", today=TODAY, window=window)
    assert plan.mode == "discovery"
    assert "2026/07/01:2026/10/01[edat]" in plan.tiers[0].pubmed_query


async def test_out_of_scope_has_no_tiers(planner: BuildPlanner) -> None:
    plan = await plan_for(planner, stored("plan_out_of_scope"), "best pizza in Boston?")
    assert (plan.mode, plan.tiers, plan.source) == ("out_of_scope", [], "llm")


async def test_unsafe_terms_are_dropped_and_queries_stay_valid(planner: BuildPlanner) -> None:
    plan = await plan_for(planner, stored("plan_unsafe_terms"), "metformin and kidneys")
    assert plan.source == "llm"
    assert_well_formed(plan)
    assert "biguanide" not in plan.tiers[0].pubmed_query
    assert '"eGFR"[tiab]' in plan.tiers[0].pubmed_query


async def test_concepts_with_no_usable_terms_fall_back(planner: BuildPlanner) -> None:
    plan = await plan_for(planner, stored("plan_no_usable_terms"), "metformin and kidneys")
    assert plan.source == "fallback"
    assert_well_formed(plan)


@pytest.mark.parametrize(
    "replies",
    [
        ["not json", "still not json"],
        [stored("plan_bad_mode"), stored("plan_bad_mode")],
        [RateLimitedError("slow down")],
    ],
    ids=["invalid-json", "schema-violation", "llm-unavailable"],
)
async def test_llm_failures_fall_back_to_a_keyword_plan(
    planner: BuildPlanner, replies: list[Reply]
) -> None:
    built, _ = planner(replies)
    plan = await built.plan(DUMBBELL, today=TODAY)
    assert (plan.source, plan.mode) == ("fallback", "question")
    assert [t.level for t in plan.tiers] == [1, 2]
    assert "(dumbbell curl barbell biceps muscle growth)" in plan.tiers[1].pubmed_query
    assert_well_formed(plan)


def test_fallback_keywords_never_contain_operators() -> None:
    plan = fallback_plan("metformin AND kidney OR NOT dialysis")
    assert "(metformin kidney dialysis)" in plan.tiers[1].pubmed_query
    assert_well_formed(plan)


def test_fallback_discovery_uses_the_window() -> None:
    plan = fallback_plan("diabetes older adults", window=DateWindow(start=date(2026, 4, 14)))
    assert plan.mode == "discovery"
    assert "2026/04/14:3000[edat]" in plan.tiers[0].pubmed_query


async def test_prompt_carries_today_and_conversation_history(planner: BuildPlanner) -> None:
    built, provider = planner([stored("plan_dumbbell")])
    await built.plan("what about in women?", history=[DUMBBELL], today=TODAY)
    sent = json.loads(provider.prompts[0])
    assert sent == {
        "today": "2026-10-14",
        "earlier_questions": [DUMBBELL],
        "question": "what about in women?",
    }
    assert "MeSH" in provider.systems[0]


async def test_plan_serializes_with_spec_field_names(planner: BuildPlanner) -> None:
    window = {"from": "2020-01-01", "to": None}
    plan = await plan_for(planner, stored("plan_dumbbell", date_range=window))
    dumped = plan.model_dump(mode="json", by_alias=True)
    assert dumped["date_range"] == {"from": "2020-01-01", "to": None}
    assert {t["level"] for t in dumped["tiers"]} == {1, 2, 3, 4}


async def test_asking_for_a_meta_analysis_also_accepts_systematic_reviews(
    planner: BuildPlanner,
) -> None:
    plan = await plan_for(planner, stored("plan_dumbbell", requested_evidence=["meta_analysis"]))
    assert plan.requested_evidence == ["meta_analysis"]
    assert '("meta-analysis"[pt] OR "systematic review"[pt])' in plan.tiers[0].pubmed_query


async def test_placeholder_pico_values_become_null(planner: BuildPlanner) -> None:
    reply = stored("plan_dumbbell", population="...", outcome="  ", intervention="dumbbell curl")
    plan = await plan_for(planner, reply)
    assert (plan.population, plan.outcome, plan.intervention) == (None, None, "dumbbell curl")


async def test_body_site_is_its_own_concept_in_the_direct_tiers(planner: BuildPlanner) -> None:
    tiers = (await plan_for(planner, stored("plan_dumbbell"))).tiers
    site = '("biceps"[tiab] OR "elbow flexor*"[tiab])'
    assert site in tiers[0].pubmed_query
    assert site in tiers[1].pubmed_query


async def test_a_window_ending_today_stays_open(planner: BuildPlanner) -> None:
    window = {"from": "2026-04-14", "to": "2026-10-14"}
    reply = stored("plan_discovery", date_range=window)
    plan = await plan_for(planner, reply, "recent research on diabetes in older people")
    assert plan.date_range is not None
    assert plan.date_range.end is None
    assert "2026/04/14:3000[edat]" in plan.tiers[0].pubmed_query


async def test_a_closed_past_window_is_kept(planner: BuildPlanner) -> None:
    window = {"from": "2015-01-01", "to": "2018-12-31"}
    plan = await plan_for(planner, stored("plan_dumbbell", date_range=window))
    assert "2015/01/01:2018/12/31[dp]" in plan.tiers[0].pubmed_query


def checked_planner(
    make_settings: Callable[..., Settings], not_found: list[str] | Exception
) -> tuple[Planner, FakeSearcher]:
    searcher = FakeSearcher(not_found)
    llm = LLMClient(make_settings(), ScriptedProvider([stored("plan_dumbbell")]))
    return Planner(llm, searcher), searcher


async def test_invented_mesh_headings_become_text_terms(
    make_settings: Callable[..., Settings],
) -> None:
    planner_, searcher = checked_planner(make_settings, ['"Electromyography"[mh]'])
    plan = await planner_.plan(DUMBBELL, today=TODAY)

    assert plan.rejected_mesh == ["Electromyography"]
    mechanistic = plan.tiers[3].pubmed_query
    assert '"Electromyography"[mh]' not in mechanistic
    assert '"electromyography"[tiab]' in mechanistic
    assert '"Resistance Training"[mh]' in mechanistic
    mesh_checks = [t for t in searcher.terms if t not in searcher.tier_searches]
    assert len(mesh_checks) == 1
    assert_well_formed(plan)


async def test_known_headings_are_kept(make_settings: Callable[..., Settings]) -> None:
    planner_, _ = checked_planner(make_settings, [])
    plan = await planner_.plan(DUMBBELL, today=TODAY)
    assert plan.rejected_mesh == []
    assert '"Electromyography"[mh]' in plan.tiers[3].pubmed_query


async def test_mesh_check_failure_keeps_headings(make_settings: Callable[..., Settings]) -> None:
    planner_, _ = checked_planner(make_settings, UpstreamError("PubMed down"))
    plan = await planner_.plan(DUMBBELL, today=TODAY)
    assert plan.source == "llm"
    assert '"Electromyography"[mh]' in plan.tiers[3].pubmed_query


BROADER = {
    "intervention": {"mesh": [], "text": ["dumbbell*"]},
    "comparison": {"mesh": [], "text": ["barbell*"]},
    "outcome": {"mesh": ["Hypertrophy"], "text": ["hypertroph*", "muscle size"]},
}


def broadening_planner(
    make_settings: Callable[..., Settings],
    replies: list[Reply],
    count: Callable[[str], int],
) -> tuple[Planner, ScriptedProvider, FakeSearcher]:
    provider = ScriptedProvider(replies)
    searcher = FakeSearcher([], count=count)
    return Planner(LLMClient(make_settings(), provider), searcher), provider, searcher


def broader_dumbbell() -> str:
    data = json.loads(stored("plan_dumbbell"))
    data["concepts"].update(BROADER)
    return json.dumps(data)


async def test_empty_direct_tiers_are_retried_once_with_broader_terms(
    make_settings: Callable[..., Settings],
) -> None:
    planner_, provider, _ = broadening_planner(
        make_settings, [stored("plan_dumbbell"), broader_dumbbell()], count=lambda _q: 0
    )
    plan = await planner_.plan(DUMBBELL, today=TODAY)

    assert plan.broadened is True
    assert '"dumbbell*"[tiab]' in plan.tiers[0].pubmed_query
    assert len(provider.prompts) == 2
    assert "no studies at all in tiers 1 and 2" in provider.prompts[1]
    assert_well_formed(plan)


async def test_direct_evidence_found_means_no_retry(
    make_settings: Callable[..., Settings],
) -> None:
    planner_, provider, searcher = broadening_planner(
        make_settings, [stored("plan_dumbbell")], count=lambda _q: 3
    )
    plan = await planner_.plan(DUMBBELL, today=TODAY)
    assert plan.broadened is False
    assert len(provider.prompts) == 1
    assert len(searcher.tier_searches) == 1


async def test_tier_two_results_alone_prevent_the_retry(
    make_settings: Callable[..., Settings],
) -> None:
    def count(query: str) -> int:
        return 0 if '"meta-analysis"[pt] OR' in query and " NOT (" not in query else 4

    planner_, provider, searcher = broadening_planner(
        make_settings, [stored("plan_dumbbell")], count=count
    )
    plan = await planner_.plan(DUMBBELL, today=TODAY)
    assert plan.broadened is False
    assert len(searcher.tier_searches) == 2
    assert len(provider.prompts) == 1


async def test_failed_retry_keeps_the_first_plan(make_settings: Callable[..., Settings]) -> None:
    planner_, _, _ = broadening_planner(
        make_settings, [stored("plan_dumbbell"), "not json", "still not"], count=lambda _q: 0
    )
    plan = await planner_.plan(DUMBBELL, today=TODAY)
    assert (plan.broadened, plan.source) == (False, "llm")
    assert '"dumbbell curl*"[tiab]' in plan.tiers[0].pubmed_query


async def test_discovery_is_never_broadened(make_settings: Callable[..., Settings]) -> None:
    planner_, provider, searcher = broadening_planner(
        make_settings, [stored("plan_discovery")], count=lambda _q: 0
    )
    plan = await planner_.plan("recent diabetes research in older adults", today=TODAY)
    assert plan.broadened is False
    assert searcher.tier_searches == []
    assert len(provider.prompts) == 1
