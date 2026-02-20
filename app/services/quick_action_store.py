from __future__ import annotations

import json
from uuid import uuid4

from redis.asyncio import Redis

from app.services.assistant_response import QuickAction


class QuickActionStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def put(self, telegram_id: int, actions: list[QuickAction]) -> str:
        token = uuid4().hex[:12]
        payload = {
            "telegram_id": telegram_id,
            "actions": [
                {
                    "label": item.label,
                    "action": item.action,
                    "payload": item.payload,
                }
                for item in actions
            ],
        }
        await self._redis.set(self._key(token), json.dumps(payload, ensure_ascii=False), ex=self._ttl)
        return token

    async def get(self, token: str) -> tuple[int, list[QuickAction]] | None:
        raw = await self._redis.get(self._key(token))
        if raw is None:
            return None
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        actions = [
            QuickAction(
                label=str(item["label"]),
                action=str(item["action"]),
                payload=dict(item.get("payload") or {}),
            )
            for item in payload.get("actions", [])
        ]
        return int(payload["telegram_id"]), actions

    async def delete(self, token: str) -> None:
        await self._redis.delete(self._key(token))

    def _key(self, token: str) -> str:
        return f"quick_action:{token}"
