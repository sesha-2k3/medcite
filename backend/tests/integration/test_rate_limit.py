from __future__ import annotations

import time
import uuid

import pytest
from redis.asyncio import Redis

from app.errors import RateLimitedError
from app.sources.rate_limit import RedisTokenBucket

pytestmark = pytest.mark.integration


def fresh_key() -> str:
    return f"ratelimit:test:{uuid.uuid4().hex}"


async def timed_acquires(bucket: RedisTokenBucket, n: int) -> float:
    started = time.monotonic()
    for _ in range(n):
        await bucket.acquire()
    return time.monotonic() - started


async def test_burst_up_to_capacity_is_immediate(redis_client: Redis) -> None:
    bucket = RedisTokenBucket(redis_client, fresh_key(), rate_per_s=5)
    assert await timed_acquires(bucket, 5) < 0.15


async def test_request_beyond_capacity_waits_for_refill(redis_client: Redis) -> None:
    bucket = RedisTokenBucket(redis_client, fresh_key(), rate_per_s=5)
    await timed_acquires(bucket, 5)
    assert await timed_acquires(bucket, 1) >= 0.15


async def test_sustained_rate_is_enforced(redis_client: Redis) -> None:
    bucket = RedisTokenBucket(redis_client, fresh_key(), rate_per_s=20, capacity=1)
    assert await timed_acquires(bucket, 11) >= 0.4


async def test_instances_with_the_same_key_share_one_budget(redis_client: Redis) -> None:
    key = fresh_key()
    first = RedisTokenBucket(redis_client, key, rate_per_s=5)
    second = RedisTokenBucket(redis_client, key, rate_per_s=5)
    await timed_acquires(first, 5)
    assert await timed_acquires(second, 1) >= 0.15


async def test_different_keys_have_independent_budgets(redis_client: Redis) -> None:
    await timed_acquires(RedisTokenBucket(redis_client, fresh_key(), rate_per_s=5), 5)
    other = RedisTokenBucket(redis_client, fresh_key(), rate_per_s=5)
    assert await timed_acquires(other, 1) < 0.1


async def test_gives_up_when_the_wait_exceeds_max_wait(redis_client: Redis) -> None:
    bucket = RedisTokenBucket(redis_client, fresh_key(), rate_per_s=1, max_wait_s=0.05)
    await bucket.acquire()
    with pytest.raises(RateLimitedError):
        await bucket.acquire()


async def test_rate_must_be_positive(redis_client: Redis) -> None:
    with pytest.raises(ValueError, match="positive"):
        RedisTokenBucket(redis_client, fresh_key(), rate_per_s=0)
