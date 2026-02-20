from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from redis.asyncio import Redis


@dataclass(slots=True)
class DialogState:
    turns: list[dict[str, str]] = field(default_factory=list)
    pending_question: str | None = None
    pending_reason: str | None = None
    scenario_type: str | None = None
    scenario_payload: dict[str, object] = field(default_factory=dict)
    scenario_expires_at: str | None = None

    def has_active_scenario(self, now_utc: datetime | None = None) -> bool:
        if not self.scenario_type:
            return False
        if not self.scenario_expires_at:
            return True
        now = now_utc or datetime.now(tz=UTC)
        try:
            return now <= datetime.fromisoformat(self.scenario_expires_at)
        except ValueError:
            return False


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
        scenario_type = payload.get("scenario_type")
        scenario_payload_raw = payload.get("scenario_payload")
        scenario_payload: dict[str, object] = {}
        if isinstance(scenario_payload_raw, dict):
            scenario_payload = scenario_payload_raw
        scenario_expires_at = payload.get("scenario_expires_at")
        return DialogState(
            turns=turns,
            pending_question=(str(pending_question) if pending_question else None),
            pending_reason=(str(pending_reason) if pending_reason else None),
            scenario_type=(str(scenario_type) if scenario_type else None),
            scenario_payload=scenario_payload,
            scenario_expires_at=(str(scenario_expires_at) if scenario_expires_at else None),
        )

    async def save(self, telegram_id: int, state: DialogState) -> None:
        payload = {
            "turns": state.turns[-self._max_turns :],
            "pending_question": state.pending_question,
            "pending_reason": state.pending_reason,
            "scenario_type": state.scenario_type,
            "scenario_payload": state.scenario_payload,
            "scenario_expires_at": state.scenario_expires_at,
        }
        await self._redis.set(self._key(telegram_id), json.dumps(payload, ensure_ascii=False), ex=self._ttl)

    async def clear(self, telegram_id: int) -> None:
        await self._redis.delete(self._key(telegram_id))

    def _key(self, telegram_id: int) -> str:
        return f"dialog_state:{telegram_id}"
