"""Wire the nodes into the pipeline graph (handoff section 7.3) and run it as an event stream.

plan -> route: retrieve_tier, or respond_scope when out of scope
retrieve_tier -> ingest_abstracts -> rank -> enough_evidence: next tier, or generate
generate -> after_generate: verify, or respond (no evidence, failed retry)
verify -> verified: generate once more on failure, else respond
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Sequence
from typing import Any, Protocol

from langgraph.graph import END, START, StateGraph

from app.graph.events import Event
from app.graph.nodes import Nodes
from app.graph.state import PipelineState, RunControl
from app.models.llm import UsageMeter
from app.services import Services


class NodeFn(Protocol):
    """A node as LangGraph types it: the parameter must be named ``state``."""

    def __call__(self, state: PipelineState) -> Awaitable[dict[str, Any]]: ...


def tracked(node: NodeFn) -> NodeFn:
    """Register the node's task with the run's ``RunControl`` while it runs."""

    async def run(state: PipelineState) -> dict[str, Any]:
        control = state.get("control")
        task = asyncio.current_task()
        if control is None or task is None:
            return await node(state)
        if control.cancelled:
            raise asyncio.CancelledError
        control.tasks.add(task)
        try:
            return await node(state)
        finally:
            control.tasks.discard(task)

    run.__name__ = getattr(node, "__name__", "node")
    return run


def build_graph(services: Services) -> Any:
    nodes = Nodes(services)
    graph = StateGraph(PipelineState)
    graph.add_node("plan", tracked(nodes.plan))
    graph.add_node("retrieve_tier", tracked(nodes.retrieve_tier))
    graph.add_node("ingest_abstracts", tracked(nodes.ingest_abstracts))
    graph.add_node("rank", tracked(nodes.rank))
    graph.add_node("generate", tracked(nodes.generate))
    graph.add_node("verify", tracked(nodes.verify))
    graph.add_node("respond", tracked(nodes.respond))
    graph.add_node("respond_scope", tracked(nodes.respond_scope))

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", nodes.route, ["retrieve_tier", "respond_scope"])
    graph.add_edge("retrieve_tier", "ingest_abstracts")
    graph.add_edge("ingest_abstracts", "rank")
    graph.add_conditional_edges("rank", nodes.enough_evidence, ["retrieve_tier", "generate"])
    graph.add_conditional_edges("generate", nodes.after_generate, ["verify", "respond"])
    graph.add_conditional_edges("verify", nodes.verified, ["generate", "respond"])
    graph.add_edge("respond", END)
    graph.add_edge("respond_scope", END)
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
        }
        async for chunk in self._graph.astream(state, stream_mode="custom"):
            if isinstance(chunk, Event):
                yield chunk
