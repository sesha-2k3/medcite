"""Wire the nodes into the pipeline graph (handoff section 7.3) and run it as an event stream.

plan -> route: retrieve_tier; discover_search for discovery; respond_scope if out of scope
discover_search -> ingest_abstracts -> cluster_themes -> summarize_themes -> respond_digest
retrieve_tier -> ingest_abstracts -> rank -> enough_evidence: next tier, or generate
generate -> after_generate: verify, or respond (no evidence, failed retry)
verify -> verified: generate once more on failure, else respond
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Sequence
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from app.generation.plan import DateWindow
from app.graph.events import Event
from app.graph.nodes import Nodes
from app.graph.state import PipelineState, RunControl
from app.models.llm import UsageMeter
from app.services import Services
from app.tracing import NoopTrace, Trace


class NodeFn(Protocol):
    """A node as LangGraph types it: the parameter must be named ``state``."""

    def __call__(self, state: PipelineState) -> Awaitable[dict[str, Any]]: ...


def tracked(name: str, node: NodeFn) -> NodeFn:
    """Run a node in its own trace span, recording the LLM calls it makes, with its task
    registered in the run's ``RunControl`` so a disconnect can cancel it.
    """

    async def run(state: PipelineState) -> dict[str, Any]:
        control = state.get("control")
        task = asyncio.current_task()
        if control is not None and control.cancelled:
            raise asyncio.CancelledError
        if control is not None and task is not None:
            control.tasks.add(task)
        trace = state.get("trace") or NoopTrace("")
        meter = state.get("meter")
        before = len(meter.calls) if meter is not None else 0
        try:
            with trace.node(name) as span:
                try:
                    return await node(state)
                finally:
                    if meter is not None:
                        span.record_llm_calls(meter.calls[before:])
        finally:
            if control is not None and task is not None:
                control.tasks.discard(task)

    run.__name__ = name
    return run


def build_graph(services: Services) -> Any:
    nodes = Nodes(services)
    graph = StateGraph(PipelineState)
    graph.add_node("plan", tracked("plan", nodes.plan))
    graph.add_node("retrieve_tier", tracked("retrieve_tier", nodes.retrieve_tier))
    graph.add_node("ingest_abstracts", tracked("ingest_abstracts", nodes.ingest_abstracts))
    graph.add_node("rank", tracked("rank", nodes.rank))
    graph.add_node("generate", tracked("generate", nodes.generate))
    graph.add_node("verify", tracked("verify", nodes.verify))
    graph.add_node("respond", tracked("respond", nodes.respond))
    graph.add_node("respond_scope", tracked("respond_scope", nodes.respond_scope))
    graph.add_node("discover_search", tracked("discover_search", nodes.discover_search))
    graph.add_node("cluster_themes", tracked("cluster_themes", nodes.cluster_themes))
    graph.add_node("summarize_themes", tracked("summarize_themes", nodes.summarize_themes))
    graph.add_node("respond_digest", tracked("respond_digest", nodes.respond_digest))

    graph.add_edge(START, "plan")
    graph.add_conditional_edges(
        "plan", nodes.route, ["retrieve_tier", "discover_search", "respond_scope"]
    )
    graph.add_edge("retrieve_tier", "ingest_abstracts")
    graph.add_edge("discover_search", "ingest_abstracts")
    graph.add_conditional_edges("ingest_abstracts", nodes.after_ingest, ["rank", "cluster_themes"])
    graph.add_conditional_edges(
        "cluster_themes", nodes.after_grouping, ["summarize_themes", "respond_digest"]
    )
    graph.add_edge("summarize_themes", "respond_digest")
    graph.add_conditional_edges("rank", nodes.enough_evidence, ["retrieve_tier", "generate"])
    graph.add_conditional_edges("generate", nodes.after_generate, ["verify", "respond"])
    graph.add_conditional_edges("verify", nodes.verified, ["generate", "respond"])
    graph.add_edge("respond", END)
    graph.add_edge("respond_scope", END)
    graph.add_edge("respond_digest", END)
    return graph.compile()


class Pipeline:
    def __init__(self, services: Services) -> None:
        self._graph = build_graph(services)

    async def run(
        self,
        *,
        question: str,
        history: Sequence[str] = (),
        user_id: uuid.UUID,
        trace_id: str,
        meter: UsageMeter | None = None,
        control: RunControl | None = None,
        trace: Trace | None = None,
        window: DateWindow | None = None,
    ) -> AsyncIterator[Event]:
        """Events in emission order. Errors propagate. ``control.cancel()`` stops the node
        that is running, so no further PubMed or LLM calls are made.
        """
        state: PipelineState = {
            "question": question,
            "history": list(history),
            "user_id": user_id,
            "trace_id": trace_id,
            "meter": meter or UsageMeter(),
            "control": control or RunControl(),
            "trace": trace or NoopTrace(trace_id),
            "started_at": time.monotonic(),
            "scores": {},
        }
        if window is not None:
            state["window"] = window
        async for chunk in self._graph.astream(state, stream_mode="custom"):
            if isinstance(chunk, Event):
                yield chunk
