"""Response cache for external sources.

The cache is an optimization, never a dependency: Redis errors are logged and treated as misses.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Credentials and caller identity don't change the response, so they never enter the key.
EXCLUDED_FROM_KEY = frozenset({"api_key", "tool", "email"})


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_s: int) -> None: ...


class RedisCache:
    def __init__(self, redis: Redis, prefix: str = "cache:") -> None:
        self._redis = redis
        self._prefix = prefix

    async def get(self, key: str) -> str | None:
        try:
            value = await self._redis.get(self._prefix + key)
        except RedisError as exc:
            logger.warning("cache_unavailable", extra={"op": "get", "error": repr(exc)})
            return None
        if isinstance(value, bytes):
            return value.decode()
        return value

    async def set(self, key: str, value: str, ttl_s: int) -> None:
        if ttl_s <= 0:
            return
        try:
            await self._redis.set(self._prefix + key, value, ex=ttl_s)
        except RedisError as exc:
            logger.warning("cache_unavailable", extra={"op": "set", "error": repr(exc)})


def cache_key(namespace: str, params: Mapping[str, object]) -> str:
    """Stable key for a request: whitespace-normalized, order-independent, credential-free."""
    canonical = {
        name: _normalize(value) for name, value in params.items() if name not in EXCLUDED_FROM_KEY
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return f"{namespace}:{hashlib.sha256(payload.encode()).hexdigest()}"


def _normalize(value: object) -> str:
    return " ".join(str(value).split())
