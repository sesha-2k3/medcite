"""Entry point for scheduled and background work (portability rule 7).

    python -m app.jobs <name>

Schedulers (Render cron, EventBridge, Kubernetes CronJob) only ever invoke this command.
Phase 1 registers no jobs; Phase 2 adds RQ enqueueing and Phase 3 the scheduled jobs.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable

from app.config import get_settings
from app.observability import configure_logging

logger = logging.getLogger("app.jobs")

# name -> callable; populated in later phases (weekly_digests, retraction_recheck, ...)
JOBS: dict[str, Callable[[], None]] = {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.jobs")
    parser.add_argument("name", help=f"job to run; available: {sorted(JOBS) or 'none yet'}")
    args = parser.parse_args(argv)

    configure_logging(get_settings().log_level)
    job = JOBS.get(args.name)
    if job is None:
        logger.error("unknown job", extra={"job": args.name, "available": sorted(JOBS)})
        return 2
    logger.info("job start", extra={"job": args.name})
    job()
    logger.info("job done", extra={"job": args.name})
    return 0


if __name__ == "__main__":
    sys.exit(main())
