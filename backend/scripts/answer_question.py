"""Answer a question, or build a digest, with the same LangGraph pipeline the API runs.

uv run python -m scripts.answer_question "dumbbell vs barbell curls for biceps growth"
uv run python -m scripts.answer_question "diabetes in older adults" --months 6

Prints each step as it runs, then the answer or digest, the plan, warnings and token usage.
Sends a Langfuse trace when keys are set. Writes no query log or usage row.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from app.config import get_settings
from app.db.redis import create_redis
from app.db.session import create_engine, create_session_factory
from app.generation.plan import DateWindow
from app.graph.build import Pipeline
from app.graph.events import Event
from app.models.llm import UsageMeter
from app.observability import configure_logging
from app.services import Resources, build_services
from app.sources.http import create_http_client
from app.tracing import build_tracer

USER = uuid.UUID(int=0)  # the script's stand-in user; nothing is written for it


async def run(question: str, per_tier: int | None, months: int | None) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    window = DateWindow(start=date.today() - timedelta(days=30 * months)) if months else None
    engine = create_engine(settings)
    redis = create_redis(settings)
    tracer = build_tracer(settings)
    meter = UsageMeter()
    try:
        async with create_http_client(settings) as http:
            print("loading MedCPT ...")
            resources = Resources(http=http, redis=redis, sessions=create_session_factory(engine))
            services = replace(await build_services(settings, resources), recorder=None)
            if per_tier is not None:
                services = replace(services, config=replace(services.config, tier_retmax=per_tier))
            trace = tracer.start(
                seed=uuid.uuid4().hex,
                name="discover" if window else "ask",
                user_id=str(USER),
                input={"question": question},
                metadata={"source": "scripts.answer_question"},
            )
            events: list[Event] = []
            try:
                async for event in Pipeline(services).run(
                    question=question,
                    user_id=USER,
                    trace_id=trace.trace_id,
                    meter=meter,
                    trace=trace,
                    window=window,
                ):
                    if event.name == "step":
                        show_step(event.data)
                    events.append(event)
            finally:
                trace.end()
            show_result(events)
            show_usage(meter, trace.trace_id if settings.langfuse_public_key else None)
    finally:
        tracer.shutdown()
        await redis.aclose()
        await engine.dispose()


def show_step(data: dict[str, Any]) -> None:
    detail = f" ({data['detail']})" if data.get("detail") else ""
    print(f"  . {data['label']}{detail}")


def show_result(events: list[Event]) -> None:
    by_name: dict[str, list[Any]] = {}
    for event in events:
        by_name.setdefault(event.name, []).append(event.data)
    for gap in by_name.get("gap", []):
        print(f"\nEVIDENCE GAP: no {gap['requested_evidence']} found")
    text = "".join(t["text"] for t in by_name.get("token", []))
    if text:
        print(f"\nANSWER\n{text}")
    for theme in by_name.get("theme", []):
        print(f"\nTHEME {theme['name']}  [{theme['evidence_mix']}]\n{theme['summary']}")
        for a in theme["articles"]:
            print(f"  [{a['marker']}] {a['pmid']}  {a['badge']:<22} {a['title'][:70]}")
    for rows in by_name.get("evidence_table", []):
        print("\nEVIDENCE TABLE")
        for r in rows:
            print(f"  {r['pmid']:>9}  {r['badge']:<22} n={r['n']!s:<5} {r['year']}  {r['finding']}")
    for rating in by_name.get("rating", []):
        print(f"\nRATING {rating['level']}: {rating['justification']}")
    for citations in by_name.get("citations", []):
        print("\nCITATIONS")
        for c in citations:
            print(f"  [{c['marker']}] PMID {c['pmid']} {c['section']}")
    for warning in by_name.get("warning", []):
        print(f"\nWARNING: {warning['message']}")
    for done in by_name.get("done", []):
        show_plan(done.get("query_plan"))


def show_plan(plan: dict[str, Any] | None) -> None:
    if not plan:
        return
    print(f"\nPLAN mode={plan['mode']} source={plan['source']} prompt={plan['prompt']}")
    fields = ("population", "condition", "intervention", "comparison", "outcome")
    print("  " + "; ".join(f"{f}: {plan[f]}" for f in fields if plan.get(f)))
    print(f"  rejected MeSH: {plan['rejected_mesh'] or 'none'}   broadened: {plan['broadened']}")
    for tier in plan["tiers"]:
        print(f"  tier {tier['level']} ({tier['purpose']}): {tier['pubmed_query']}")


def show_usage(meter: UsageMeter, trace_id: str | None) -> None:
    print("\nLLM CALLS")
    for call in meter.calls:
        print(
            f"  {call.role.value:<9} {call.model:<24} in {call.input_tokens:>6,} "
            f"out {call.output_tokens:>6,}  {call.latency_ms:>6,} ms"
        )
    total = sum(c.input_tokens + c.output_tokens for c in meter.calls)
    print(f"  total tokens {total:,}, cost ${meter.cost_usd}")
    if trace_id:
        print(f"\nLangfuse trace: {trace_id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.answer_question")
    parser.add_argument("question")
    parser.add_argument("--per-tier", type=int, help="PMIDs per tier (default TIER_RETMAX)")
    parser.add_argument("--months", type=int, help="build a digest of the last N months")
    args = parser.parse_args()
    asyncio.run(run(args.question, args.per_tier, args.months))


if __name__ == "__main__":
    main()
