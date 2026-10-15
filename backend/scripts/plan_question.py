"""Plan a question with the real planner and count PubMed matches per tier.

uv run python -m scripts.plan_question "dumbbell vs barbell curls for biceps growth"
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import get_settings
from app.db.redis import create_redis
from app.generation.plan import Planner
from app.models.llm import build_llm_client
from app.observability import configure_logging
from app.sources.factory import build_pubmed_client
from app.sources.http import create_http_client


async def run(question: str) -> None:
    settings = get_settings()
    configure_logging("WARNING")
    redis = create_redis(settings)
    try:
        async with create_http_client(settings) as http:
            pubmed = build_pubmed_client(settings, http, redis)
            planner = Planner(build_llm_client(settings), pubmed)
            plan = await planner.plan(question)

            print(f"\nmode: {plan.mode}   source: {plan.source}   outcome: {plan.outcome_class}")
            print(f"P: {plan.population}\ncondition: {plan.condition}\nI: {plan.intervention}")
            print(f"C: {plan.comparison}\nO: {plan.outcome}")
            print(f"requested evidence: {plan.requested_evidence or 'none'}")
            print(f"rejected MeSH: {plan.rejected_mesh or 'none'}")
            print(f"broadened after an empty first search: {'yes' if plan.broadened else 'no'}")
            print(f"standalone: {plan.standalone_query}\n")
            if not plan.tiers:
                print("no tiers (out of scope)")
                return
            for tier in plan.tiers:
                result = await pubmed.esearch(tier.pubmed_query, retmax=0)
                print(f"tier {tier.level} ({tier.purpose}): {result.count:,} matches")
                print(f"  {tier.pubmed_query}")
                for warning in result.warnings:
                    print(f"  warning: {warning}")
                print()
    finally:
        await redis.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.plan_question")
    parser.add_argument("question")
    asyncio.run(run(parser.parse_args().question))


if __name__ == "__main__":
    main()
