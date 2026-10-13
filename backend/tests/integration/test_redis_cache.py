from __future__ import annotations

import pytest
from redis.asyncio import Redis

from app.sources.cache import RedisCache

pytestmark = pytest.mark.integration


async def test_round_trip_with_ttl(redis_client: Redis) -> None:
    cache = RedisCache(redis_client, prefix="test:")
    await cache.set("k", "<PubmedArticleSet/>", ttl_s=60)
    assert await cache.get("k") == "<PubmedArticleSet/>"
    assert 0 < await redis_client.ttl("test:k") <= 60


async def test_missing_key_is_none(redis_client: Redis) -> None:
    assert await RedisCache(redis_client).get("absent") is None
