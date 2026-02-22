from __future__ import annotations

import json
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.services.assistant.assistant_response import ConfirmationRequest


class ConfirmationStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 600) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def put(self, telegram_id: int, request: ConfirmationRequest) -> str:
        token = uuid4().hex[:12]
        key = self._key(token)
        payload = {
            "telegram_id": telegram_id,
            "action": request.action,
            "command_payload": request.command_payload,
            "event_id": str(request.event_id) if request.event_id is not None else None,
            "summary": request.summary,
        }
        await self._redis.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl)
        return token

    async def get(self, token: str) -> tuple[int, ConfirmationRequest] | None:
        raw = await self._redis.get(self._key(token))
        if raw is None:
            return None
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        request = ConfirmationRequest(
            action=str(payload["action"]),
            command_payload=dict(payload["command_payload"]),
            event_id=(UUID(str(payload["event_id"])) if payload.get("event_id") is not None else None),
            summary=str(payload.get("summary", "")),
        )
        return int(payload["telegram_id"]), request

    async def delete(self, token: str) -> None:
        await self._redis.delete(self._key(token))

    def _key(self, token: str) -> str:
        return f"confirm:{token}"
