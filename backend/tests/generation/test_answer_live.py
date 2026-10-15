"""The real generator and judge inside the pipeline, on synthetic passages (no database, no
PubMed).

    uv run pytest -m live tests/generation/test_answer_live.py -s

Uses your Groq key: two to four generator and judge calls. On the free plan the
regeneration may wait for the per-minute token limit to reset.
"""

from __future__ import annotations

import uuid

import pytest

from app.config import Settings
from app.generation.factory import context_budget
from app.generation.gap import states_gap
from app.generation.generate import Generator
from app.generation.verify import Judge, Verifier, first_sentence
from app.graph.build import Pipeline
from app.models.llm import UsageMeter, build_llm_client
from app.services import PipelineConfig, Services
from tests.graph.fakes import (
    PRIMARY,
    FakeIngester,
    FakePlanner,
    FakeRecorder,
    FakeRetriever,
    FakeSearcher,
    found_by_search,
    question_plan,
)

pytestmark = pytest.mark.live


@pytest.fixture
def settings() -> Settings:
    settings = Settings()
    if settings.llm_api_key is None:
        pytest.skip("LLM_API_KEY not set")
    return settings


async def test_gap_and_conflict_on_synthetic_evidence(settings: Settings) -> None:
    llm = build_llm_client(settings)
    recorder = FakeRecorder()
    services = Services(
        planner=FakePlanner(question_plan()),
        searcher=FakeSearcher({"q1": PRIMARY}),
        ingester=FakeIngester(),
        retriever=FakeRetriever(found_by_search),
        generator=Generator(llm, max_tokens=settings.generation_max_tokens),
        verifier=Verifier(Judge(llm, max_tokens=settings.judge_max_tokens)),
        config=PipelineConfig(enough_relevant_passages=3, budget=context_budget(settings)),
        recorder=recorder,
    )
    meter = UsageMeter()
    async for _ in Pipeline(services).run(
        question="Does creatine improve grip strength?",
        user_id=uuid.uuid4(),
        trace_id="live",
        meter=meter,
    ):
        pass
    [entry] = recorder.entries
    assert entry.answer is not None
    assert entry.verification is not None
    answer, verification = entry.answer, entry.verification

    print(f"\n{answer['direct_answer']}\n\n{answer['synthesis']}\n")
    for item in verification["issues"]:
        print(f"{item['severity']}: {item['kind']} at {item['location']}: {item['message']}")
    for call in meter.calls:
        print(f"{call.role.value}: in {call.input_tokens}, out {call.output_tokens}")

    assert states_gap(first_sentence(answer["direct_answer"]), [])
    assert all(i["kind"] != "unknown_citation" for i in verification["issues"])
    cited = {c["marker"] for c in answer["citations"]}
    assert {"C1", "C3"} <= cited, "both directions of the conflicting evidence are cited"
