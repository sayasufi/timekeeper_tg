from __future__ import annotations

from redis.asyncio import Redis


class IdempotencyStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def register_once(self, key: str) -> bool:
        # True means first-seen key, False means duplicate.
        result = await self._redis.set(self._key(key), "1", ex=self._ttl, nx=True)
        return bool(result)

    def _key(self, key: str) -> str:
        return f"idem:{key}"
