"""Graph nodes and edge functions. Each node is a thin wrapper over existing services and
emits a ``step`` event when it starts.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, Literal

from app.db.queries.query_logs import CompletedQuery
from app.errors import AppError
from app.generation.answer import Answer, VerificationResult, issue
from app.generation.context import build_context
from app.generation.gap import detect_evidence_gap, requested_evidence_label
from app.generation.service import no_evidence, with_gap_fallback, with_issue
from app.generation.verify import user_warnings
from app.graph.events import emit, emit_step, token_chunks
from app.graph.state import PipelineState
from app.services import Services

logger = logging.getLogger(__name__)

Update = dict[str, Any]
MAX_ATTEMPTS = 2
OUT_OF_SCOPE = (
    "MedCite answers questions from the biomedical research literature in PubMed. This "
    "question appears to be outside that scope, so no search was run."
)
DISCOVERY_LATER = (
    "Themed digests of recent research arrive in a later version; this is answered as a question."
)


class Nodes:
    def __init__(self, services: Services) -> None:
        self._s = services

    async def plan(self, state: PipelineState) -> Update:
        emit_step("plan", "Planning the search")
        plan = await self._s.planner.plan(
            state["question"], history=state.get("history", []), meter=state.get("meter")
        )
        return {
            "plan": plan,
            "mode": plan.mode,
            "tier_index": 0,
            "candidates": {},
            "passages": [],
            "retries": 0,
        }

    async def retrieve_tier(self, state: PipelineState) -> Update:
        plan, index = state["plan"], state["tier_index"]
        tier = plan.tiers[index]
        if index == 0 and plan.mode == "discovery":
            emit("warning", {"message": DISCOVERY_LATER})
        emit_step("retrieve_tier", f"Searching PubMed: {tier.purpose}", f"tier {tier.level}")
        found = await self._s.searcher.esearch(tier.pubmed_query, retmax=self._s.config.tier_retmax)
        candidates = dict(state["candidates"])
        new = [pmid for pmid in found.pmids if pmid not in candidates]
        candidates.update({pmid: tier.level for pmid in new})
        return {"candidates": candidates, "new_pmids": new, "tier_index": index + 1}

    async def ingest_abstracts(self, state: PipelineState) -> Update:
        new = state.get("new_pmids", [])
        noun = "article" if len(new) == 1 else "articles"
        emit_step("ingest_abstracts", "Reading new abstracts", f"{len(new)} new {noun}")
        if new:
            await self._s.ingester.ingest(new, meter=state.get("meter"))
        return {"new_pmids": []}

    async def rank(self, state: PipelineState) -> Update:
        emit_step("rank", "Ranking passages")
        plan = state["plan"]
        outcome = None if plan.outcome_class == "unknown" else plan.outcome_class
        passages = await self._s.retriever.retrieve(
            plan.standalone_query,
            candidate_tiers=state["candidates"] or None,
            question_outcome=outcome,
            score_cache=state.get("scores"),
        )
        return {"passages": passages}

    def enough_evidence(self, state: PipelineState) -> Literal["retrieve_tier", "generate"]:
        """Only passages from this question's own search count: articles stored by earlier
        questions must not stop the search before the lower tiers are tried.
        """
        config = self._s.config
        relevant = sum(
            1
            for p in state["passages"]
            if p.tier is not None and p.scored.relevance > config.relevance_threshold
        )
        tiers_left = state["tier_index"] < len(state["plan"].tiers)
        if relevant < config.enough_relevant_passages and tiers_left:
            return "retrieve_tier"
        return "generate"

    async def generate(self, state: PipelineState) -> Update:
        plan, passages = state["plan"], state["passages"]
        previous = state.get("verification")
        if previous is None:
            emit_step("generate", "Writing the answer")
            context = build_context(passages, self._s.config.budget)
            gap = detect_evidence_gap(passages, plan.requested_evidence)
            if gap:
                label = requested_evidence_label(plan.requested_evidence)
                emit("gap", {"requested_evidence": label})
            if not context.items:
                answer, result = no_evidence(plan)
                return {
                    "context": context,
                    "evidence_gap": True,
                    "draft": answer,
                    "verification": result,
                }
            answer = await self._s.generator.generate(
                plan.standalone_query,
                context,
                plan=plan,
                evidence_gap=gap,
                meter=state.get("meter"),
            )
            return {"context": context, "evidence_gap": gap, "draft": answer}

        emit_step("generate", "Revising the answer", "fixing checks that failed")
        retries = state.get("retries", 0) + 1
        try:
            answer = await self._s.generator.generate(
                plan.standalone_query,
                state["context"],
                plan=plan,
                evidence_gap=state["evidence_gap"],
                feedback=previous.errors,
                meter=state.get("meter"),
            )
        except AppError as exc:
            note = issue("regeneration_failed", "answer", f"retry failed: {exc.message}")
            return {
                "verification": with_issue(previous, note),
                "retry_failed": True,
                "retries": retries,
            }
        return {"draft": answer, "retries": retries}

    @staticmethod
    def after_generate(state: PipelineState) -> Literal["verify", "respond"]:
        verification = state.get("verification")
        if state.get("retry_failed") or (verification is not None and verification.attempts == 0):
            return "respond"
        return "verify"

    async def verify(self, state: PipelineState) -> Update:
        emit_step("verify", "Checking claims against the sources")
        attempt = state.get("retries", 0) + 1
        result = await self._s.verifier.verify(
            state["plan"].standalone_query,
            state["draft"],
            state["context"],
            attempt=attempt,
            judge_on_errors=attempt >= MAX_ATTEMPTS,
            meter=state.get("meter"),
        )
        return {"verification": result}

    @staticmethod
    def verified(state: PipelineState) -> Literal["generate", "respond"]:
        result = state["verification"]
        return "generate" if not result.passed and result.attempts < MAX_ATTEMPTS else "respond"

    async def respond(self, state: PipelineState) -> Update:
        emit_step("respond", "Preparing the answer")
        answer, result = with_gap_fallback(state["draft"], state["verification"])
        text = answer.direct_answer
        if answer.synthesis:
            text = f"{text}\n\n{answer.synthesis}"
        for chunk in token_chunks(text):
            emit("token", {"text": chunk})
        emit("citations", [c.model_dump(mode="json") for c in answer.citations])
        emit("evidence_table", [row.model_dump(mode="json") for row in answer.evidence_table])
        emit("rating", {"level": answer.rating.level, "justification": answer.rating.justification})
        for message in user_warnings(result):
            emit("warning", {"message": message})
        trace = state.get("trace")
        if trace is not None:
            trace.update(
                output={"direct_answer": answer.direct_answer, "rating": answer.rating.level},
                metadata={"evidence_gap": answer.evidence_gap, "verified": result.passed},
            )
        query_id = await self._record(state, answer, result)
        _emit_done(state, query_id)
        return {"draft": answer, "verification": result}

    async def respond_scope(self, state: PipelineState) -> Update:
        emit_step("respond", "Preparing the answer")
        emit("token", {"text": OUT_OF_SCOPE})
        _emit_done(state, await self._record(state, None, None))
        return {}

    async def _record(
        self, state: PipelineState, answer: Answer | None, result: VerificationResult | None
    ) -> int | None:
        """Fails open: the answer is already delivered, so a failed log write only loses
        the row (and with it, feedback for this answer).
        """
        recorder = self._s.recorder
        if recorder is None:
            return None
        plan, meter = state.get("plan"), state.get("meter")
        context = state.get("context")
        started = state.get("started_at", time.monotonic())
        entry = CompletedQuery(
            user_id=state["user_id"],
            mode=plan.mode if plan else None,
            user_query=state["question"],
            plan=plan.model_dump(mode="json", by_alias=True) if plan else None,
            candidate_pmids=sorted(state.get("candidates", {})),
            context_chunk_ids=[i.chunk.chunk_id for i in context.items] if context else [],
            answer=answer.model_dump(mode="json") if answer else None,
            evidence_gap=answer.evidence_gap if answer else None,
            verification=result.model_dump(mode="json") if result else None,
            latency_ms=round((time.monotonic() - started) * 1000),
            cost_usd=meter.cost_usd if meter else Decimal(0),
            trace_id=state.get("trace_id", ""),
        )
        try:
            return await recorder.record(entry)
        except Exception:
            logger.exception("query_log_failed")
            return None

    @staticmethod
    def route(state: PipelineState) -> Literal["retrieve_tier", "respond_scope"]:
        plan = state["plan"]
        if plan.mode == "out_of_scope" or not plan.tiers:
            return "respond_scope"
        return "retrieve_tier"


def _emit_done(state: PipelineState, query_id: int | None) -> None:
    plan = state.get("plan")
    emit(
        "done",
        {
            "trace_id": state.get("trace_id", ""),
            "query_id": query_id,
            "query_plan": plan.model_dump(mode="json", by_alias=True) if plan else None,
        },
    )
