from __future__ import annotations

import json
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.services.assistant.assistant_response import AmbiguityOption, AmbiguityRequest


class AmbiguityStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def put(self, telegram_id: int, request: AmbiguityRequest) -> str:
        token = uuid4().hex[:12]
        key = self._key(token)
        payload = {
            "telegram_id": telegram_id,
            "action": request.action,
            "command_payload": request.command_payload,
            "options": [
                {
                    "event_id": str(item.event_id),
                    "title": item.title,
                    "subtitle": item.subtitle,
                }
                for item in request.options
            ],
        }
        await self._redis.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl)
        return token

    async def get(self, token: str) -> tuple[int, AmbiguityRequest] | None:
        key = self._key(token)
        raw = await self._redis.get(key)
        if raw is None:
            return None

        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        request = AmbiguityRequest(
            action=str(payload["action"]),
            command_payload=dict(payload["command_payload"]),
            options=[
                AmbiguityOption(
                    event_id=UUID(str(item["event_id"])),
                    title=str(item["title"]),
                    subtitle=str(item["subtitle"]),
                )
                for item in payload["options"]
            ],
        )
        return int(payload["telegram_id"]), request

    async def delete(self, token: str) -> None:
        await self._redis.delete(self._key(token))

    def _key(self, token: str) -> str:
        return f"ambiguity:{token}"