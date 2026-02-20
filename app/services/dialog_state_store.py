from __future__ import annotations

import json
from dataclasses import dataclass, field

from redis.asyncio import Redis


@dataclass(slots=True)
class DialogState:
    turns: list[dict[str, str]] = field(default_factory=list)
    pending_question: str | None = None
    pending_reason: str | None = None


class DialogStateStore:
    def __init__(self, redis: Redis, ttl_seconds: int = 86400, max_turns: int = 8) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._max_turns = max_turns

    async def get(self, telegram_id: int) -> DialogState:
        raw = await self._redis.get(self._key(telegram_id))
        if raw is None:
            return DialogState()
        payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
        turns_raw = payload.get("turns", [])
        turns: list[dict[str, str]] = []
        if isinstance(turns_raw, list):
            for item in turns_raw[-self._max_turns :]:
                if isinstance(item, dict):
                    role = str(item.get("role", "user"))
                    content = str(item.get("content", "")).strip()
                    if content:
                        turns.append({"role": role, "content": content})
        pending_question = payload.get("pending_question")
        pending_reason = payload.get("pending_reason")
        return DialogState(
            turns=turns,
            pending_question=(str(pending_question) if pending_question else None),
            pending_reason=(str(pending_reason) if pending_reason else None),
        )

    async def save(self, telegram_id: int, state: DialogState) -> None:
        payload = {
            "turns": state.turns[-self._max_turns :],
            "pending_question": state.pending_question,
            "pending_reason": state.pending_reason,
        }
        await self._redis.set(self._key(telegram_id), json.dumps(payload, ensure_ascii=False), ex=self._ttl)

    async def clear(self, telegram_id: int) -> None:
        await self._redis.delete(self._key(telegram_id))

    def _key(self, telegram_id: int) -> str:
        return f"dialog_state:{telegram_id}"
