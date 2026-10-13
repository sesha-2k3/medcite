"""Wiring for source clients; the only place concrete adapters are chosen."""

from __future__ import annotations

import httpx
from redis.asyncio import Redis

from app.config import Settings
from app.sources.cache import Cache, RedisCache
from app.sources.pubmed import PubMedClient, PubMedConfig, RetryPolicy
from app.sources.rate_limit import RedisTokenBucket

NCBI_RATE_LIMIT_KEY = "ratelimit:ncbi"


def build_pubmed_client(
    settings: Settings, http: httpx.AsyncClient, redis: Redis, cache: Cache | None = None
) -> PubMedClient:
    return PubMedClient(
        http=http,
        limiter=RedisTokenBucket(redis, NCBI_RATE_LIMIT_KEY, rate_per_s=settings.ncbi_max_rps),
        cache=cache or RedisCache(redis),
        config=PubMedConfig.from_settings(settings),
        retry=RetryPolicy(attempts=settings.ncbi_retry_attempts),
    )
