"""Request rate limiting shared by every process that talks to NCBI."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.errors import RateLimitedError, UpstreamError

# Token bucket evaluated atomically in Redis using the server clock, so every process shares
# one budget and clock skew between hosts cannot inflate it. Returns ms to wait (0 = granted).
_BUCKET_SCRIPT = """
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1]) or capacity
local ts = tonumber(state[2]) or now
tokens = math.min(capacity, tokens + (now - ts) * rate / 1000)
local wait = 0
if tokens >= 1 then
  tokens = tokens - 1
else
  wait = math.ceil((1 - tokens) * 1000 / rate)
end
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('PEXPIRE', KEYS[1], math.ceil(capacity * 1000 / rate) + 1000)
return wait
"""


class RateLimiter(Protocol):
    async def acquire(self) -> None:
        """Return once the caller may send one request."""
        ...


class RedisTokenBucket:
    """A token bucket stored in Redis under ``key``, refilled at ``rate_per_s``."""

    def __init__(
        self,
        redis: Redis,
        key: str,
        rate_per_s: float,
        capacity: int | None = None,
        max_wait_s: float = 30.0,
    ) -> None:
        if rate_per_s <= 0:
            raise ValueError("rate_per_s must be positive")
        self._key = key
        self._rate = rate_per_s
        self._capacity = capacity or max(1, int(rate_per_s))
        self._max_wait_s = max_wait_s
        self._script = redis.register_script(_BUCKET_SCRIPT)

    async def acquire(self) -> None:
        deadline = time.monotonic() + self._max_wait_s
        while True:
            wait_s = await self._try_take() / 1000
            if wait_s <= 0:
                return
            if time.monotonic() + wait_s > deadline:
                raise RateLimitedError(
                    "Timed out waiting for the request budget", retry_after_s=wait_s
                )
            await asyncio.sleep(wait_s)

    async def _try_take(self) -> int:
        try:
            return int(await self._script(keys=[self._key], args=[self._rate, self._capacity]))
        except RedisError as exc:
            raise UpstreamError(f"Rate limiter unavailable: {exc!r}") from exc
