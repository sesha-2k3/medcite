from __future__ import annotations

from typing import Any

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.sources.cache import RedisCache, cache_key


class BrokenRedis:
    async def get(self, *_args: Any, **_kwargs: Any) -> None:
        raise RedisConnectionError("down")

    async def set(self, *_args: Any, **_kwargs: Any) -> None:
        raise RedisConnectionError("down")


class RecordingRedis:
    def __init__(self) -> None:
        self.sets: list[tuple[str, str, int]] = []

    async def set(self, key: str, value: str, ex: int) -> None:
        self.sets.append((key, value, ex))


def test_key_ignores_parameter_order_and_whitespace() -> None:
    a = cache_key("ns", {"term": "biceps   curl", "retmax": 20})
    b = cache_key("ns", {"retmax": "20", "term": " biceps curl "})
    assert a == b


def test_key_ignores_credentials_and_identity() -> None:
    plain = cache_key("ns", {"term": "x"})
    with_identity = cache_key("ns", {"term": "x", "api_key": "k", "tool": "t", "email": "e"})
    assert plain == with_identity


def test_key_keeps_case_because_pubmed_operators_are_case_sensitive() -> None:
    assert cache_key("ns", {"term": "a AND b"}) != cache_key("ns", {"term": "a and b"})


def test_key_is_namespaced() -> None:
    assert cache_key("pubmed:esearch.fcgi", {"term": "x"}).startswith("pubmed:esearch.fcgi:")
    assert cache_key("a", {"term": "x"}) != cache_key("b", {"term": "x"})


async def test_redis_errors_are_treated_as_misses() -> None:
    cache = RedisCache(BrokenRedis())  # type: ignore[arg-type]
    assert await cache.get("k") is None
    await cache.set("k", "v", ttl_s=60)


@pytest.mark.parametrize("ttl_s", [0, -1])
async def test_non_positive_ttl_skips_the_write(ttl_s: int) -> None:
    redis = RecordingRedis()
    await RedisCache(redis).set("k", "v", ttl_s=ttl_s)  # type: ignore[arg-type]
    assert redis.sets == []


async def test_writes_use_prefix_and_ttl() -> None:
    redis = RecordingRedis()
    await RedisCache(redis, prefix="c:").set("k", "v", ttl_s=60)  # type: ignore[arg-type]
    assert redis.sets == [("c:k", "v", 60)]
