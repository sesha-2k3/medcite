from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from app.config import Settings
from app.errors import RateLimitedError
from app.generation.plan import DateWindow, Plan, Tier
from app.graph.build import Pipeline
from app.graph.events import Event, token_chunks
from app.graph.state import RunControl
from app.ingest.service import IngestReport
from app.models.llm import UsageMeter
from app.retrieval.retriever import EvidencePassage
from app.services import PipelineConfig
from app.tracing import NoopTrace
from tests.discovery.builders import GLUCOSE, MUSCLE, all_articles
from tests.generation.builders import creatine_passages, stored
from tests.graph.fakes import (
    ALL,
    PRIMARY,
    FakeIngester,
    FakeRecorder,
    FakeRetriever,
    FakeSearcher,
    fake_services,
    found_by_search,
    question_plan,
)
from tests.llm_fakes import Reply, ScriptedProvider

GAP_JUDGE = stored("judge_supported", gap_statement_ok=True)


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


async def test_completed_runs_are_logged_and_done_carries_the_query_id(build: Build) -> None:
    recorder = FakeRecorder(query_id=42)
    pipeline, _ = build(
        [stored("generate_conflict"), stored("judge_supported")],
        searcher=FakeSearcher({"q1": ALL}),
        retriever=FakeRetriever(found_by_search),
        config=PipelineConfig(enough_relevant_passages=1),
        recorder=recorder,
    )
    events = await collect(pipeline)
    assert events[-1].data["query_id"] == 42
    [entry] = recorder.entries
    assert entry.mode == "question"
    assert entry.candidate_pmids == sorted(ALL)
    assert entry.context_chunk_ids == [11, 12, 21, 31]
    assert entry.trace_id == "t1"
    assert entry.answer is not None
    assert entry.answer["evidence_table"][0]["pmid"] == 90_000_001
    assert entry.verification is not None
    assert entry.verification["passed"] is True
    assert entry.latency_ms >= 0


async def test_a_failed_log_write_still_finishes_the_answer(build: Build) -> None:
    pipeline, _ = build(
        [],
        plan=question_plan(mode="out_of_scope", tiers=[]),
        recorder=FakeRecorder(error=OSError("database down")),
    )
    events = await collect(pipeline)
    assert events[-1].name == "done"
    assert events[-1].data["query_id"] is None


async def test_scores_are_shared_across_tiers(build: Build) -> None:
    retriever = FakeRetriever(found_by_search)
    pipeline, _ = build(
        [stored("generate_gap"), GAP_JUDGE],
        searcher=FakeSearcher({"q1": PRIMARY}),
        retriever=retriever,
        config=PipelineConfig(enough_relevant_passages=5),
    )
    await collect(pipeline)
    assert len(retriever.caches) == 4
    first = retriever.caches[0]
    assert first is not None
    assert all(cache is first for cache in retriever.caches)


class RecordingSpan:
    def __init__(self, spans: list[tuple[str, int]], name: str) -> None:
        self._spans = spans
        self._name = name

    def record_llm_calls(self, calls: Sequence[object]) -> None:
        self._spans.append((self._name, len(calls)))


class RecordingTrace(NoopTrace):
    def __init__(self) -> None:
        super().__init__("trace-1")
        self.spans: list[tuple[str, int]] = []

    def node(self, name: str) -> nullcontext[RecordingSpan]:  # type: ignore[override]
        return nullcontext(RecordingSpan(self.spans, name))


async def test_each_node_is_traced_with_its_llm_calls(build: Build) -> None:
    pipeline, _ = build(
        [stored("generate_conflict"), stored("judge_supported")],
        searcher=FakeSearcher({"q1": ALL}),
        retriever=FakeRetriever(found_by_search),
        config=PipelineConfig(enough_relevant_passages=1),
    )
    trace = RecordingTrace()
    async for _ in pipeline.run(question="q", user_id=uuid.uuid4(), trace_id="t", trace=trace):
        pass
    assert trace.spans == [
        ("plan", 0),
        ("retrieve_tier", 0),
        ("ingest_abstracts", 0),
        ("rank", 0),
        ("generate", 1),
        ("verify", 1),
        ("respond", 0),
    ]


DISCOVERY_REPLIES: list[Reply] = [
    stored("themes_two"),
    stored("digest_glucose"),
    stored("digest_muscle"),
    stored("judge_empty"),
]


def discovery_plan() -> Plan:
    return question_plan(
        mode="discovery",
        date_range=DateWindow(start=date(2026, 4, 14)),
        tiers=[Tier(level=1, purpose="recent research", pubmed_query="recent")],
    )


async def test_discovery_streams_named_themes(build: Build) -> None:
    recorder = FakeRecorder(query_id=9)
    searcher = FakeSearcher({"recent": [*GLUCOSE, *MUSCLE]}, counts={"recent": 5900})
    ingester = FakeIngester()
    pipeline, _ = build(
        DISCOVERY_REPLIES,
        plan=discovery_plan(),
        searcher=searcher,
        ingester=ingester,
        recorder=recorder,
        discovery_articles=all_articles(),
    )
    events = await collect(pipeline)
    assert steps(events) == [
        "plan",
        "discover_search",
        "ingest_abstracts",
        "cluster_themes",
        "summarize_themes",
        "summarize_themes",
        "respond",
    ]
    themes = [e.data for e in events if e.name == "theme"]
    assert [t["name"] for t in themes] == [
        "Continuous glucose monitoring in the oldest patients",
        "GLP-1 drugs and muscle health",
    ]
    assert all(len(t["articles"]) >= 2 for t in themes)
    assert all(a["badge"] for t in themes for a in t["articles"])
    assert names(events)[-3:] == ["theme", "citations", "done"]
    assert ingester.batches == [[*GLUCOSE, *MUSCLE]]
    assert searcher.calls[0]["ttl"] == 3600
    assert events[-1].data["query_id"] == 9
    [entry] = recorder.entries
    assert entry.mode == "discovery"
    assert entry.answer is not None
    assert len(entry.answer["themes"]) == 2


async def test_discovery_without_themes_says_so(build: Build) -> None:
    pipeline, provider = build(
        [], plan=discovery_plan(), searcher=FakeSearcher({"recent": []}), discovery_articles=[]
    )
    events = await collect(pipeline)
    assert "summarize_themes" not in steps(events)
    token = next(e for e in events if e.name == "token")
    assert "No themes could be formed" in token.data["text"]
    assert provider.prompts == []


def test_token_chunks_rejoin_exactly() -> None:
    text = "A sentence with several words [C1].\n\nAnother paragraph that is fairly long."
    chunks = token_chunks(text, size=12)
    assert "".join(chunks) == text
    assert all(len(c) <= 12 for c in chunks)


GAP_SENTENCE = "No systematic review or meta-analysis addressing this question was found in PubMed."
type Answered = tuple[list[Event], dict[str, Any], dict[str, Any], ScriptedProvider]


async def answered(build: Build, replies: list[Reply], *, gap: bool = False) -> Answered:
    """Run one question to the end; return its events, the logged answer and verification,
    and the scripted provider. With ``gap``, tier 1 finds no synthesis.
    """
    recorder = FakeRecorder()
    pipeline, provider = build(
        replies,
        searcher=FakeSearcher({"q1": PRIMARY if gap else ALL}),
        retriever=FakeRetriever(found_by_search),
        config=PipelineConfig(enough_relevant_passages=3 if gap else 1),
        recorder=recorder,
    )
    events = await collect(pipeline)
    [entry] = recorder.entries
    assert entry.answer is not None
    assert entry.verification is not None
    assert isinstance(provider, ScriptedProvider)
    return events, entry.answer, entry.verification, provider


def kinds(verification: dict[str, Any], severity: str | None = None) -> list[str]:
    return [i["kind"] for i in verification["issues"] if severity in (None, i["severity"])]


def warnings(events: list[Event]) -> list[str]:
    return [e.data["message"] for e in events if e.name == "warning"]


async def test_conflicting_evidence_answer_reports_both_directions(build: Build) -> None:
    events, answer, verification, provider = await answered(
        build, [stored("generate_conflict"), stored("judge_supported")]
    )
    assert verification["passed"] is True
    assert verification["attempts"] == 1
    assert "increased by 2.1 kg" in answer["synthesis"]
    assert "no association" in answer["synthesis"]
    assert {"C1", "C3"} <= {c["marker"] for c in answer["citations"]}
    assert warnings(events) == []
    assert len(provider.prompts) == 2
    assert provider.systems[0].startswith("You write evidence summaries")
    assert provider.systems[1].startswith("You check an evidence summary")


async def test_omitted_conflict_triggers_one_regeneration(build: Build) -> None:
    one_sided = "A small randomized trial found that grip strength increased by 2.1 kg [C1]."
    _, answer, verification, provider = await answered(
        build,
        [
            stored("generate_conflict", synthesis=one_sided),
            stored("judge_supported", conflict_omitted=True, conflict_note="C1 vs C3"),
            stored("generate_conflict"),
            stored("judge_supported"),
        ],
    )
    assert verification["passed"] is True
    assert verification["attempts"] == 2
    assert "YOUR PREVIOUS ATTEMPT FAILED THESE CHECKS" in provider.prompts[2]
    assert "C1 vs C3" in provider.prompts[2]
    assert "no association" in answer["synthesis"]


async def test_deterministic_failure_skips_the_first_judge_call(build: Build) -> None:
    _, _, verification, provider = await answered(
        build,
        [
            stored("generate_conflict", synthesis="Grip strength rose by 2.1 kg [C9]."),
            stored("generate_conflict"),
            stored("judge_supported"),
        ],
    )
    assert verification["passed"] is True
    assert verification["attempts"] == 2
    assert len(provider.prompts) == 3
    assert "cites C9" in provider.prompts[1]


async def test_persistent_failure_returns_with_a_warning(build: Build) -> None:
    bad = stored("generate_conflict", synthesis="Grip strength rose by 9.9 kg [C1].")
    events, _, verification, _ = await answered(build, [bad, bad, stored("judge_supported")])
    assert verification["passed"] is False
    assert kinds(verification, "error") == ["number_not_in_source"]
    assert warnings(events) == ["Some statements could not be verified against the cited passages."]


async def test_gap_prompts_tell_the_generator_and_judge(build: Build) -> None:
    _, answer, verification, provider = await answered(
        build, [stored("generate_gap"), GAP_JUDGE], gap=True
    )
    assert verification["passed"] is True
    assert answer["direct_answer"].startswith("No systematic review or meta-analysis")
    assert "EVIDENCE GAP: yes" in provider.prompts[0]
    assert "EVIDENCE GAP: yes" in provider.prompts[1]


async def test_code_states_the_gap_when_the_model_never_does(build: Build) -> None:
    silent = stored("generate_gap", direct_answer="Creatine may help grip strength [C1].")
    events, answer, verification, _ = await answered(
        build, [silent, silent, stored("judge_supported", gap_statement_ok=False)], gap=True
    )
    assert answer["direct_answer"].startswith(GAP_SENTENCE)
    text = "".join(e.data["text"] for e in events if e.name == "token")
    assert text.startswith(GAP_SENTENCE)
    assert verification["passed"] is True
    assert kinds(verification, "warning") == ["gap_statement_inserted"]


async def test_failed_regeneration_keeps_the_first_answer(build: Build) -> None:
    first = stored("generate_conflict", synthesis="Grip strength rose by 2.1 kg [C9].")
    _, answer, verification, _ = await answered(build, [first, RateLimitedError("slow down")])
    assert answer["synthesis"] == "Grip strength rose by 2.1 kg [C9]."
    assert kinds(verification) == ["unknown_citation", "regeneration_failed"]
    assert verification["passed"] is False
