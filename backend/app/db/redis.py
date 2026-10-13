"""Redis connection.

Redis holds only reconstructable data (portability rule 8): the PubMed cache and the shared
NCBI rate limiter in Phase 1, plus the job queue from Phase 2.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import Settings


def create_redis(settings: Settings) -> Redis:
    """Create the process-wide async Redis client (connects lazily)."""
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_connect_timeout_s,
        socket_timeout=settings.redis_connect_timeout_s,
        health_check_interval=30,
    )


async def ping_redis(client: Redis) -> None:
    """Raise if Redis is unreachable."""
    await client.ping()
