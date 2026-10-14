"""Answer a question end to end against real services, until M7's pipeline exists.

uv run python -m scripts.answer_question "dumbbell vs barbell curls for biceps growth" --per-tier 20

plan → for each tier: ESearch, ingest new PMIDs, retrieve → stop once enough passages look
relevant → evidence gap → generate → verify (one regeneration) → print everything, including
estimated and reported prompt tokens.
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.db.redis import create_redis
from app.db.session import create_engine, create_session_factory
from app.generation.factory import build_answer_service
from app.generation.gap import detect_evidence_gap, requested_evidence_label
from app.generation.plan import Plan, Planner
from app.generation.service import AnsweredQuestion
from app.ingest.factory import build_ingest_service
from app.models.cross_encoder import load_cross_encoder
from app.models.embeddings import load_embedder
from app.models.llm import UsageMeter, build_llm_client
from app.observability import configure_logging
from app.retrieval.factory import build_retriever
from app.retrieval.retriever import EvidencePassage
from app.sources.factory import build_pubmed_client
from app.sources.http import create_http_client

# Cross-encoder logits above 0 looked relevant in M4; calibrated in M10.
RELEVANT_LOGIT = 0.0


async def run(question: str, per_tier: int) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    print("loading MedCPT ...")
    embedder = await asyncio.to_thread(load_embedder, settings)
    scorer = await asyncio.to_thread(load_cross_encoder, settings)
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    redis = create_redis(settings)
    meter = UsageMeter()
    try:
        async with create_http_client(settings) as http:
            pubmed = build_pubmed_client(settings, http, redis)
            llm = build_llm_client(settings)
            plan = await Planner(llm, pubmed).plan(question, meter=meter)
            show_plan(plan)
            if plan.mode == "out_of_scope" or not plan.tiers:
                print("out of scope: no search")
                return
            if plan.mode == "discovery":
                print("discovery mode arrives in M8; answering as a question\n")

            ingest = build_ingest_service(settings, pubmed, llm, embedder, sessions)
            retriever = build_retriever(settings, sessions, embedder, scorer)
            outcome = None if plan.outcome_class == "unknown" else plan.outcome_class
            candidates: dict[int, int] = {}
            passages: list[EvidencePassage] = []
            for tier in plan.tiers:
                found = await pubmed.esearch(tier.pubmed_query, retmax=per_tier)
                new = [pmid for pmid in found.pmids if pmid not in candidates]
                candidates.update({pmid: tier.level for pmid in new})
                await ingest.ingest(new, meter=meter)
                passages = await retriever.retrieve(
                    plan.standalone_query, candidate_tiers=candidates, question_outcome=outcome
                )
                relevant = sum(1 for p in passages if p.scored.relevance > RELEVANT_LOGIT)
                print(
                    f"tier {tier.level}: {found.count:,} matches, {len(new)} new PMIDs, "
                    f"{len(passages)} passages kept, {relevant} above logit {RELEVANT_LOGIT}"
                )
                if relevant >= settings.context_min_chunks:
                    break

            gap = detect_evidence_gap(passages, plan.requested_evidence)
            print(f"evidence gap: {'yes' if gap else 'no'}\n")
            service = build_answer_service(settings, llm)
            result = await service.answer(
                question, passages, evidence_gap=gap, plan=plan, meter=meter
            )
            show_answer(result)
            show_usage(meter, result)
    finally:
        await redis.aclose()
        await engine.dispose()


def show_plan(plan: Plan) -> None:
    print(f"\nmode: {plan.mode}   source: {plan.source}   outcome: {plan.outcome_class}")
    print(f"requested evidence: {requested_evidence_label(plan.requested_evidence)}")
    print(f"rejected MeSH: {plan.rejected_mesh or 'none'}   broadened: {plan.broadened}\n")


def show_answer(result: AnsweredQuestion) -> None:
    answer, check, context = result.answer, result.verification, result.context
    print(
        f"context: {len(context.items)} passages, about {context.estimated_tokens} tokens, "
        f"{context.omitted} left out by the budget\n"
    )
    print(f"ANSWER\n{answer.direct_answer}\n\n{answer.synthesis}\n")
    print("EVIDENCE TABLE")
    for row in answer.evidence_table:
        print(f"  {row.pmid:>9}  {row.badge:<22} n={row.n!s:<5} {row.year}  {row.finding}")
    lowered = ", lowered" if answer.rating.clamped else ""
    print(
        f"\nRATING {answer.rating.level} (ceiling {answer.rating.ceiling}{lowered}): "
        f"{answer.rating.justification}\n"
    )
    print("CITATIONS")
    for citation in answer.citations:
        print(f"  [{citation.marker}] PMID {citation.pmid} {citation.section}")
    print(
        f"\nVERIFICATION passed={check.passed} attempts={check.attempts} judge={check.judge_status}"
    )
    for item in check.issues:
        print(f"  {item.severity}: {item.kind} at {item.location}: {item.message}")
    for warning in result.warnings:
        print(f"  WARNING: {warning}")


def show_usage(meter: UsageMeter, result: AnsweredQuestion) -> None:
    print(f"\nLLM CALLS (context estimate {result.context.estimated_tokens} tokens)")
    for call in meter.calls:
        print(
            f"  {call.role.value:<9} {call.model:<24} in {call.input_tokens:>6,} "
            f"out {call.output_tokens:>6,}  {call.latency_ms:>6,} ms"
        )
    total = sum(c.input_tokens + c.output_tokens for c in meter.calls)
    print(f"  total tokens {total:,}, cost ${meter.cost_usd}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.answer_question")
    parser.add_argument("question")
    parser.add_argument("--per-tier", type=int, default=20, help="PMIDs fetched per tier")
    args = parser.parse_args()
    asyncio.run(run(args.question, args.per_tier))


if __name__ == "__main__":
    main()
