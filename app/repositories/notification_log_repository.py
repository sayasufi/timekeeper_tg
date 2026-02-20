from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NotificationLog


class NotificationLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def mark_sent(
        self,
        user_id: int,
        event_id: UUID,
        occurrence_at: datetime,
        offset_minutes: int,
    ) -> bool:
        async with self._session.begin_nested():
            log = NotificationLog(
                user_id=user_id,
                event_id=event_id,
                occurrence_at=occurrence_at,
                offset_minutes=offset_minutes,
            )
            self._session.add(log)
            try:
                await self._session.flush()
                return True
            except IntegrityError:
                return False

    async def was_sent(self, event_id: UUID, occurrence_at: datetime, offset_minutes: int) -> bool:
        stmt = select(NotificationLog).where(
            NotificationLog.event_id == event_id,
            NotificationLog.occurrence_at == occurrence_at,
            NotificationLog.offset_minutes == offset_minutes,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None
