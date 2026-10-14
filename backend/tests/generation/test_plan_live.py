"""The real planner on the reference questions.

    uv run pytest -m live tests/generation/test_plan_live.py

Uses your Groq key; two planner calls.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.generation.plan import Planner
from app.generation.pubmed_query import validate_query
from app.models.llm import build_llm_client

pytestmark = pytest.mark.live


@pytest.fixture
def planner() -> Planner:
    settings = Settings()
    if settings.llm_api_key is None:
        pytest.skip("LLM_API_KEY not set")
    return Planner(build_llm_client(settings))


async def test_dumbbell_versus_barbell_plan(planner: Planner) -> None:
    plan = await planner.plan(
        "Is there a meta-analysis of dumbbell curl vs barbell curl for biceps muscle growth?"
    )
    assert plan.source == "llm"
    assert plan.mode == "question"
    assert [t.level for t in plan.tiers] == [1, 2, 3, 4]
    assert '"meta-analysis"[pt]' in plan.tiers[0].pubmed_query
    for tier in plan.tiers:
        assert validate_query(tier.pubmed_query) == []
        assert "[tiab]" in tier.pubmed_query


async def test_diabetes_discovery_plan(planner: Planner) -> None:
    plan = await planner.plan("Recent research on diabetes in older people in the last 6 months")
    assert plan.mode in ("discovery", "hybrid")
    assert all("[edat]" in t.pubmed_query for t in plan.tiers)
