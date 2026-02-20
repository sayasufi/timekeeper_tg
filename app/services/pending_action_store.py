from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis


@dataclass(slots=True)
class PendingAction:
    action: str
    event_id: UUID


class PendingActionStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 900) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def put(self, telegram_id: int, action: PendingAction) -> None:
        payload = {
            "action": action.action,
            "event_id": str(action.event_id),
        }
        await self._redis.set(self._key(telegram_id), json.dumps(payload, ensure_ascii=False), ex=self._ttl)

    async def get(self, telegram_id: int) -> PendingAction | None:
        raw = await self._redis.get(self._key(telegram_id))
        if raw is None:
            return None
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        return PendingAction(action=str(payload["action"]), event_id=UUID(str(payload["event_id"])))

    async def clear(self, telegram_id: int) -> None:
        await self._redis.delete(self._key(telegram_id))

    def _key(self, telegram_id: int) -> str:
        return f"pending_action:{telegram_id}"
