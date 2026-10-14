"""The pipeline's state (handoff section 7.3).

The request is held as ``question`` and ``history`` rather than the API's request model, so
the graph does not depend on the HTTP layer.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, TypedDict

from app.generation.answer import Answer, VerificationResult
from app.generation.context import EvidenceContext
from app.generation.plan import Mode, Plan
from app.models.llm import UsageMeter
from app.retrieval.retriever import EvidencePassage


@dataclass(slots=True)
class RunControl:
    """Cancels a run's node tasks. LangGraph runs each node in its own asyncio task, which
    cancelling the stream's consumer does not reach, so nodes register themselves here.
    """

    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True
        for task in list(self.tasks):
            task.cancel()


class PipelineState(TypedDict, total=False):
    question: str
    history: list[str]
    user_id: uuid.UUID
    trace_id: str
    meter: UsageMeter
    mode: Mode
    plan: Plan
    tier_index: int  # the next tier to search
    candidates: dict[int, int]  # pmid -> tier that found it
    new_pmids: list[int]
    passages: list[EvidencePassage]
    evidence_gap: bool
    context: EvidenceContext
    draft: Answer
    verification: VerificationResult
    retries: int
    retry_failed: bool
    control: RunControl
