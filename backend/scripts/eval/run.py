"""Run the evaluation set through the LangGraph pipeline, as `make answer` does, and report.

uv run python -m scripts.eval.run                          run what eval/reports/baseline lacks
uv run python -m scripts.eval.run --report v2 --token-cap 120000
uv run python -m scripts.eval.run --only nordic_hamstring,dumbbell_barbell
uv run python -m scripts.eval.run --compare baseline v2

Each finished question is appended to eval/reports/NAME.jsonl, so a run stopped by the token
cap or an error resumes where it left off; NAME.json and NAME.md are rewritten after every
question. A small recorder keeps what the respond node would log; nothing reaches query_logs
or usage.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.models import Article, Chunk
from app.db.queries.query_logs import CompletedQuery
from app.db.redis import create_redis
from app.db.session import create_engine, create_session_factory
from app.graph.build import Pipeline
from app.models.llm import UsageMeter
from app.observability import configure_logging
from app.services import Resources, build_services
from app.sources.http import create_http_client
from app.tracing import build_tracer
from scripts.eval.metrics import (
    EvalQuestion,
    QuestionResult,
    compare,
    load_dataset,
    load_results,
    pending,
    write_summary,
)

REPO = Path(__file__).resolve().parents[3]
DATASET = REPO / "eval" / "datasets" / "smoke.jsonl"
REPORTS = REPO / "eval" / "reports"
USER = uuid.UUID(int=0)  # stand-in user; nothing is written for it


class CapturingRecorder:
    """Keeps the entry the respond node would write to query_logs."""

    def __init__(self) -> None:
        self.entry: CompletedQuery | None = None

    async def record(self, entry: CompletedQuery) -> int:
        self.entry = entry
        return 0


async def ranked_pmids(
    sessions: async_sessionmaker[AsyncSession], chunk_ids: list[int]
) -> list[int]:
    """The context's articles, best first: chunk IDs keep the context's rank order."""
    if not chunk_ids:
        return []
    statement = (
        select(Chunk.id, Article.pmid)
        .join(Article, Article.id == Chunk.article_id)
        .where(Chunk.id.in_(chunk_ids))
    )
    async with sessions() as session:
        by_chunk = {chunk_id: pmid for chunk_id, pmid in (await session.execute(statement)).all()}
    ordered = [by_chunk[c] for c in chunk_ids if by_chunk.get(c) is not None]
    return list(dict.fromkeys(ordered))


def tokens_by_model(meter: UsageMeter) -> dict[str, int]:
    totals: dict[str, int] = {}
    for call in meter.calls:
        totals[call.model] = totals.get(call.model, 0) + call.input_tokens + call.output_tokens
    return totals


def to_result(
    question: EvalQuestion, entry: CompletedQuery, meter: UsageMeter, ranked: list[int]
) -> QuestionResult:
    plan: dict[str, Any] = entry.plan or {}
    answer: dict[str, Any] = entry.answer or {}
    verification: dict[str, Any] = entry.verification or {}
    return QuestionResult(
        id=question.id,
        kind=question.kind,
        question=question.question,
        expected_gap=question.expected_gap,
        gold_pmids=question.gold_pmids,
        reported_gap=entry.evidence_gap,
        broadened=bool(plan.get("broadened")),
        candidate_pmids=entry.candidate_pmids,
        ranked_pmids=ranked,
        cited_pmids=list(dict.fromkeys(c["pmid"] for c in answer.get("citations", []))),
        verification_passed=verification.get("passed"),
        judge_status=verification.get("judge_status"),
        attempts=int(verification.get("attempts", 1)),
        issue_kinds=[issue["kind"] for issue in verification.get("issues", [])],
        latency_ms=entry.latency_ms,
        tokens_by_model=tokens_by_model(meter),
    )


def select_questions(
    dataset: list[EvalQuestion], done: list[QuestionResult], only: set[str] | None
) -> list[EvalQuestion]:
    if only:
        unknown = only - {row.id for row in dataset}
        if unknown:
            sys.exit(f"unknown question ids: {', '.join(sorted(unknown))}")
    return [row for row in pending(dataset, done) if not only or row.id in only]


async def run(report: str, token_cap: int, only: set[str] | None) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{report}.jsonl"
    results = load_results(path)
    todo = select_questions(load_dataset(DATASET), results, only)
    if not todo:
        print(f"nothing left to run in {path.name}")
        write_summary(REPORTS, report, results)
        return

    engine, redis, tracer = create_engine(settings), create_redis(settings), build_tracer(settings)
    spent = 0
    try:
        async with create_http_client(settings) as http:
            print("loading MedCPT ...")
            sessions = create_session_factory(engine)
            services = await build_services(
                settings, Resources(http=http, redis=redis, sessions=sessions)
            )
            for index, question in enumerate(todo, 1):
                if spent >= token_cap:
                    print(f"token cap reached ({spent:,} >= {token_cap:,}); rerun to resume")
                    break
                print(f"\n[{index}/{len(todo)}] {question.id}: {question.question}")
                recorder, meter = CapturingRecorder(), UsageMeter()
                trace = tracer.start(
                    seed=uuid.uuid4().hex,
                    name="eval",
                    user_id=str(USER),
                    input={"question": question.question},
                    metadata={"source": "scripts.eval.run", "report": report, "id": question.id},
                )
                try:
                    pipeline = Pipeline(replace(services, recorder=recorder))
                    async for event in pipeline.run(
                        question=question.question,
                        user_id=USER,
                        trace_id=trace.trace_id,
                        meter=meter,
                        trace=trace,
                    ):
                        if event.name == "step":
                            print(f"   . {event.data['label']}")
                except Exception as exc:
                    # Stop rather than skip: the question stays pending and runs on resume.
                    print(f"   failed: {exc!r}\nstopping; rerun to resume")
                    break
                finally:
                    trace.end()
                    spent += sum(tokens_by_model(meter).values())
                if recorder.entry is None:
                    print("   the pipeline recorded no result; stopping")
                    break
                ranked = await ranked_pmids(sessions, recorder.entry.context_chunk_ids)
                result = to_result(question, recorder.entry, meter, ranked)
                with path.open("a") as file:
                    file.write(result.model_dump_json() + "\n")
                results.append(result)
                write_summary(REPORTS, report, results)
                print(
                    f"   gap {result.reported_gap} (expected {question.expected_gap}), "
                    f"verified {result.verification_passed}, {result.tokens:,} tokens"
                )
    finally:
        tracer.shutdown()
        await redis.aclose()
        await engine.dispose()
    print(f"\n{len(results)} questions in {report}; spent {spent:,} tokens this run")
    print(f"report: {REPORTS / (report + '.md')}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.eval.run")
    parser.add_argument("--report", default="baseline", help="report name in eval/reports")
    parser.add_argument("--token-cap", type=int, default=150_000, help="stop before exceeding")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        before, after = (load_results(REPORTS / f"{name}.jsonl") for name in args.compare)
        print("\n".join(compare(before, after)))
        return
    only = {item.strip() for item in args.only.split(",") if item.strip()} if args.only else None
    asyncio.run(run(args.report, args.token_cap, only))


if __name__ == "__main__":
    main()
