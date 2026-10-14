from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.models.llm import LLMResponse, LLMRole
from app.tracing import LangfuseTracer, NoopTracer, build_tracer

TRACE_ID = "0123456789abcdef0123456789abcdef"


class FakeObservation:
    def __init__(self, log: list[tuple[str, str, dict[str, Any]]], name: str) -> None:
        self.log = log
        self.name = name

    def start_observation(self, *, name: str, **options: Any) -> FakeObservation:
        self.log.append(("start", name, options))
        return FakeObservation(self.log, name)

    def update(self, **options: Any) -> None:
        self.log.append(("update", self.name, options))

    def end(self) -> None:
        self.log.append(("end", self.name, {}))


class FakeClient:
    def __init__(self, fail_on: str | None = None) -> None:
        self.log: list[tuple[str, str, dict[str, Any]]] = []
        self.fail_on = fail_on
        self.closed = False

    def create_trace_id(self, *, seed: str) -> str:
        return TRACE_ID

    def start_observation(self, *, name: str, **options: Any) -> Any:
        if self.fail_on == "start":
            raise RuntimeError("langfuse down")
        self.log.append(("start", name, options))
        root = FakeObservation(self.log, name)
        if self.fail_on == "node":
            root.start_observation = self._broken  # type: ignore[method-assign]
        return root

    @staticmethod
    def _broken(**options: Any) -> Any:
        raise RuntimeError("export failed")

    def shutdown(self) -> None:
        self.closed = True


def call(role: LLMRole = LLMRole.GENERATOR) -> LLMResponse:
    return LLMResponse(
        role=role,
        model="openai/gpt-oss-120b",
        text='{"direct_answer": "x"}',
        input_tokens=3000,
        output_tokens=900,
        latency_ms=2500,
        cost_usd=Decimal("0.0012"),
    )


def test_trace_records_nodes_and_generations() -> None:
    client = FakeClient()
    trace = LangfuseTracer(client).start(seed="req-1", name="ask", user_id="u1", input={"q": 1})
    assert trace.trace_id == TRACE_ID
    with trace.node("generate") as span:
        span.record_llm_calls([call()])
    trace.update(output={"rating": "low"})
    trace.end()

    root_start = client.log[0]
    assert root_start[:2] == ("start", "ask")
    assert root_start[2]["trace_context"] == {"trace_id": TRACE_ID}
    names = [(kind, name) for kind, name, _ in client.log]
    assert names == [
        ("start", "ask"),
        ("start", "generate"),
        ("start", "llm:generator"),
        ("end", "llm:generator"),
        ("end", "generate"),
        ("update", "ask"),
        ("end", "ask"),
    ]
    generation = client.log[2][2]
    assert generation["as_type"] == "generation"
    assert generation["usage_details"] == {"input": 3000, "output": 900}
    assert generation["cost_details"] == {"total": 0.0012}


def test_a_failing_start_falls_back_to_the_request_id() -> None:
    trace = LangfuseTracer(FakeClient(fail_on="start")).start(
        seed="req-1", name="ask", user_id="u1", input=None
    )
    assert trace.trace_id == "req-1"
    with trace.node("plan") as span:
        span.record_llm_calls([call()])
    trace.end()


def test_sdk_errors_mid_trace_never_reach_the_request() -> None:
    client = FakeClient(fail_on="node")
    trace = LangfuseTracer(client).start(seed="r", name="ask", user_id="u", input=None)
    with trace.node("plan") as span:
        span.record_llm_calls([call()])
    trace.update(output="x")
    trace.end()
    assert ("end", "ask", {}) not in client.log  # the trace stopped recording after the error


def test_without_keys_tracing_is_off(make_settings: Callable[..., Settings]) -> None:
    tracer = build_tracer(make_settings())
    assert isinstance(tracer, NoopTracer)
    assert tracer.start(seed="req-9", name="ask", user_id="u", input=None).trace_id == "req-9"
