from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRunTrace


class AgentRunTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, trace: AgentRunTrace) -> AgentRunTrace:
        self._session.add(trace)
        await self._session.flush()
        return trace

    async def quality_snapshot(self, days: int = 7, user_id: int | None = None) -> dict[str, float]:
        since = datetime.now(tz=UTC) - timedelta(days=max(1, days))
        stmt = select(AgentRunTrace).where(AgentRunTrace.created_at >= since)
        if user_id is not None:
            stmt = stmt.where(AgentRunTrace.user_id == user_id)
        result = await self._session.execute(stmt)
        rows = list(result.scalars())
        total = len(rows)
        if total == 0:
            return {"parse_success": 0.0, "clarification_rate": 0.0, "wrong_action_rate": 0.0, "total": 0.0}

        clarifications = sum(1 for item in rows if item.result_intent == "clarify")
        successes = total - clarifications
        wrong_actions = 0
        for item in rows:
            if item.result_intent == "clarify":
                continue
            has_error_stage = any((stage.get("stage") == "error") for stage in item.stages if isinstance(stage, dict))
            if has_error_stage or item.confidence < 0.45:
                wrong_actions += 1
        return {
            "parse_success": round(successes / total, 4),
            "clarification_rate": round(clarifications / total, 4),
            "wrong_action_rate": round(wrong_actions / total, 4),
            "total": float(total),
        }
