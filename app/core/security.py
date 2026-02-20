from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import cast

from redis.asyncio import Redis


class FloodControl:
    def __init__(self, redis: Redis, requests_per_minute: int) -> None:
        self._redis = redis
        self._limit = requests_per_minute

    async def allow(self, user_id: int) -> bool:
        key = f"flood:{user_id}"
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, 60)
        return count <= self._limit


class IdempotencyGuard:
    def __init__(self, redis: Redis, ttl_seconds: int = 3600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def check_and_set(self, key: str) -> bool:
        result = await self._redis.set(key, "1", ex=self._ttl, nx=True)
        return cast(bool, result)


async def with_redis_lock(
    redis: Redis,
    lock_key: str,
    ttl_seconds: int,
    fn: Callable[[], Awaitable[None]],
) -> bool:
    lock = redis.lock(lock_key, timeout=ttl_seconds)
    acquired = await lock.acquire(blocking=False)
    if not acquired:
        return False
    try:
        await fn()
    finally:
        await lock.release()
    return True
