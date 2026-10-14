from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace

import pytest

from app.config import Settings
from app.errors import RateLimitedError
from app.graph.build import Pipeline
from app.graph.events import Event, token_chunks
from app.graph.state import RunControl
from app.ingest.service import IngestReport
from app.models.llm import UsageMeter
from app.retrieval.retriever import EvidencePassage
from app.services import PipelineConfig
from tests.generation.builders import creatine_passages, stored
from tests.graph.fakes import (
    FakeIngester,
    FakeRetriever,
    FakeSearcher,
    fake_services,
    question_plan,
)
from tests.llm_fakes import Reply

PRIMARY = [90_000_001, 90_000_002]  # the trial and the cohort: no synthesis in tier 1
ALL = [*PRIMARY, 90_000_003]
GAP_JUDGE = stored("judge_supported", gap_statement_ok=True)


def found_by_search(candidates: Mapping[int, int]) -> list[EvidencePassage]:
    """The creatine passages, tagged with the tier that found each article."""
    return [replace(p, tier=candidates.get(p.scored.chunk.pmid)) for p in creatine_passages()]


async def collect(pipeline: Pipeline) -> list[Event]:
    return [
        event
        async for event in pipeline.run(
            question="Does creatine improve grip strength?", user_id=uuid.uuid4(), trace_id="t1"
        )
    ]


def names(events: list[Event]) -> list[str]:
    """Event names with consecutive repeats collapsed."""
    out: list[str] = []
    for event in events:
        if not out or out[-1] != event.name:
            out.append(event.name)
    return out


def steps(events: list[Event]) -> list[str]:
    return [e.data["node"] for e in events if e.name == "step"]


Build = Callable[..., tuple[Pipeline, object]]


@pytest.fixture
def build(make_settings: Callable[..., Settings]) -> Build:
    def _build(replies: list[Reply], **kwargs: object) -> tuple[Pipeline, object]:
        services, provider = fake_services(make_settings(), replies, **kwargs)
        return Pipeline(services), provider

    return _build


async def test_events_stream_in_contract_order(build: Build) -> None:
    pipeline, _ = build(
        [stored("generate_gap"), GAP_JUDGE],
        searcher=FakeSearcher({"q1": PRIMARY}),
        retriever=FakeRetriever(found_by_search),
        config=PipelineConfig(enough_relevant_passages=3),
    )
    events = await collect(pipeline)
    assert names(events) == [
        "step",
        "gap",
        "step",
        "token",
        "citations",
        "evidence_table",
        "rating",
        "done",
    ]
    assert steps(events) == [
        "plan",
        "retrieve_tier",
        "ingest_abstracts",
        "rank",
        "generate",
        "verify",
        "respond",
    ]
    text = "".join(e.data["text"] for e in events if e.name == "token")
    assert text.startswith("No systematic review or meta-analysis has examined")
    assert "\n\nA small randomized trial" in text
    done = events[-1].data
    assert done["trace_id"] == "t1"
    assert [t["level"] for t in done["query_plan"]["tiers"]] == [1, 2, 3, 4]
    gap = next(e for e in events if e.name == "gap")
    assert gap.data == {"requested_evidence": "systematic review or meta-analysis"}


async def test_too_few_relevant_passages_moves_to_the_next_tier(build: Build) -> None:
    searcher = FakeSearcher({"q1": PRIMARY, "q2": PRIMARY})
    ingester = FakeIngester()
    pipeline, _ = build(
        [stored("generate_gap"), GAP_JUDGE],
        searcher=searcher,
        ingester=ingester,
        retriever=FakeRetriever(found_by_search),
        config=PipelineConfig(enough_relevant_passages=5),
    )
    await collect(pipeline)
    assert searcher.terms == ["q1", "q2", "q3", "q4"]
    assert ingester.batches == [PRIMARY]  # later tiers found nothing new


async def test_passages_from_earlier_questions_do_not_stop_the_search(build: Build) -> None:
    searcher = FakeSearcher({})
    pipeline, _ = build(
        [stored("generate_gap"), GAP_JUDGE],
        searcher=searcher,
        retriever=FakeRetriever(lambda _: creatine_passages_without_tiers()),
        config=PipelineConfig(enough_relevant_passages=1),
    )
    await collect(pipeline)
    assert searcher.terms == ["q1", "q2", "q3", "q4"]


def creatine_passages_without_tiers() -> list[EvidencePassage]:
    return [replace(p, tier=None) for p in creatine_passages()]


async def test_failed_checks_regenerate_once(build: Build) -> None:
    bad = stored("generate_conflict", synthesis="Grip strength rose by 2.1 kg [C9].")
    pipeline, _ = build(
        [bad, stored("generate_conflict"), stored("judge_supported")],
        searcher=FakeSearcher({"q1": ALL}),
        retriever=FakeRetriever(found_by_search),
        config=PipelineConfig(enough_relevant_passages=1),
    )
    events = await collect(pipeline)
    assert steps(events)[-5:] == ["generate", "verify", "generate", "verify", "respond"]
    labels = [e.data["label"] for e in events if e.name == "step" and e.data["node"] == "generate"]
    assert labels == ["Writing the answer", "Revising the answer"]
    assert not any(e.name == "warning" for e in events)


async def test_out_of_scope_skips_the_search(build: Build) -> None:
    searcher = FakeSearcher({})
    plan = question_plan(mode="out_of_scope", tiers=[])
    pipeline, _ = build([], plan=plan, searcher=searcher)
    events = await collect(pipeline)
    assert names(events) == ["step", "token", "done"]
    assert searcher.terms == []
    token = next(e for e in events if e.name == "token")
    assert "outside that scope" in token.data["text"]


async def test_no_passages_answers_without_a_model(build: Build) -> None:
    pipeline, _ = build([], config=PipelineConfig(enough_relevant_passages=1))
    events = await collect(pipeline)
    assert "verify" not in steps(events)
    text = "".join(e.data["text"] for e in events if e.name == "token")
    assert text.startswith("No systematic review or meta-analysis addressing this question")


async def test_discovery_questions_are_answered_with_a_warning(build: Build) -> None:
    plan = question_plan(mode="discovery", tiers=question_plan().tiers[:1])
    pipeline, _ = build([], plan=plan)
    events = await collect(pipeline)
    assert events[1].name == "warning"


async def test_generator_failure_propagates(build: Build) -> None:
    pipeline, _ = build(
        [RateLimitedError("slow down")],
        searcher=FakeSearcher({"q1": ALL}),
        retriever=FakeRetriever(found_by_search),
        config=PipelineConfig(enough_relevant_passages=1),
    )
    with pytest.raises(RateLimitedError):
        await collect(pipeline)


class SlowIngester(FakeIngester):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancelled = False

    async def ingest(
        self, pmids: Sequence[int], *, refresh: bool = False, meter: UsageMeter | None = None
    ) -> IngestReport:
        self.started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return IngestReport(requested=len(pmids), already_stored=0)


async def test_cancelling_a_run_stops_the_running_node(build: Build) -> None:
    ingester, searcher = SlowIngester(), FakeSearcher({"q1": ALL})
    pipeline, _ = build([], searcher=searcher, ingester=ingester)
    control = RunControl()

    async def consume() -> None:
        async for _ in pipeline.run(
            question="q", user_id=uuid.uuid4(), trace_id="t", control=control
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(ingester.started.wait(), timeout=5)
    control.cancel()
    await asyncio.wait([task], timeout=5)
    assert task.done()
    assert task.cancelled() or task.exception() is not None
    assert ingester.cancelled is True
    assert searcher.terms == ["q1"]


def test_token_chunks_rejoin_exactly() -> None:
    text = "A sentence with several words [C1].\n\nAnother paragraph that is fairly long."
    chunks = token_chunks(text, size=12)
    assert "".join(chunks) == text
    assert all(len(c) <= 12 for c in chunks)
