from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DueNotification


class DueNotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        user_id: int,
        event_id: UUID,
        occurrence_at: datetime,
        offset_minutes: int,
        trigger_at: datetime,
    ) -> DueNotification:
        existing = await self.get_by_unique(event_id, occurrence_at, offset_minutes)
        if existing is not None:
            existing.trigger_at = trigger_at
            existing.status = "pending"
            await self._session.flush()
            return existing

        item = DueNotification(
            user_id=user_id,
            event_id=event_id,
            occurrence_at=occurrence_at,
            offset_minutes=offset_minutes,
            trigger_at=trigger_at,
            status="pending",
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_by_unique(
        self,
        event_id: UUID,
        occurrence_at: datetime,
        offset_minutes: int,
    ) -> DueNotification | None:
        stmt = select(DueNotification).where(
            DueNotification.event_id == event_id,
            DueNotification.occurrence_at == occurrence_at,
            DueNotification.offset_minutes == offset_minutes,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_due(self, end_utc: datetime, limit: int = 500) -> list[DueNotification]:
        stmt = (
            select(DueNotification)
            .where(DueNotification.status == "pending", DueNotification.trigger_at <= end_utc)
            .order_by(DueNotification.trigger_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def mark_processing(self, item: DueNotification) -> None:
        item.status = "processing"
        await self._session.flush()

    async def mark_done(self, item: DueNotification) -> None:
        item.status = "done"
        await self._session.flush()

    async def mark_pending(self, item: DueNotification, trigger_at: datetime, occurrence_at: datetime) -> None:
        item.status = "pending"
        item.trigger_at = trigger_at
        item.occurrence_at = occurrence_at
        await self._session.flush()

    async def delete_for_event(self, event_id: UUID) -> int:
        stmt = select(DueNotification).where(DueNotification.event_id == event_id)
        result = await self._session.execute(stmt)
        items = list(result.scalars())
        for item in items:
            await self._session.delete(item)
        return len(items)

    async def touch_stuck_processing(self, older_than_minutes: int = 10) -> int:
        threshold = datetime.now(tz=UTC)
        stmt = select(DueNotification).where(DueNotification.status == "processing")
        result = await self._session.execute(stmt)
        items = list(result.scalars())
        touched = 0
        for item in items:
            if (threshold - item.updated_at).total_seconds() >= older_than_minutes * 60:
                item.status = "pending"
                touched += 1
        if touched:
            await self._session.flush()
        return touched
