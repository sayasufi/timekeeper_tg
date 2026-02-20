from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRunTrace


class AgentRunTraceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, trace: AgentRunTrace) -> AgentRunTrace:
        self._session.add(trace)
        await self._session.flush()
        return trace
