from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event: Event) -> Event:
        self._session.add(event)
        await self._session.flush()
        return event

    async def get_for_user(self, user_id: int, event_id: UUID) -> Event | None:
        stmt = select(Event).where(Event.id == event_id, Event.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, event_id: UUID) -> Event | None:
        stmt = select(Event).where(Event.id == event_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int, only_active: bool = True) -> list[Event]:
        stmt = select(Event).where(Event.user_id == user_id)
        if only_active:
            stmt = stmt.where(Event.is_active.is_(True))
        result = await self._session.execute(stmt.order_by(Event.starts_at))
        return list(result.scalars())

    async def list_active(self) -> list[Event]:
        stmt = select(Event).where(Event.is_active.is_(True)).order_by(Event.starts_at)
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def list_active_lessons_for_user(self, user_id: int) -> list[Event]:
        stmt = (
            select(Event)
            .where(Event.user_id == user_id, Event.event_type == "lesson", Event.is_active.is_(True))
            .order_by(Event.starts_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def find_by_title(self, user_id: int, search_text: str) -> Event | None:
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.is_active.is_(True),
                Event.title.ilike(f"%{search_text}%"),
            )
            .order_by(Event.starts_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def find_many_by_title(self, user_id: int, search_text: str, limit: int = 10) -> list[Event]:
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.is_active.is_(True),
                Event.title.ilike(f"%{search_text}%"),
            )
            .order_by(Event.starts_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def update(self, event: Event) -> Event:
        await self._session.flush()
        return event

    async def soft_delete(self, event: Event) -> None:
        event.is_active = False
        await self._session.flush()

    async def delete_user_events(self, user_id: int) -> int:
        events = await self.list_for_user(user_id=user_id, only_active=False)
        for event in events:
            await self._session.delete(event)
        return len(events)

    async def list_window(self, user_id: int, start_utc: datetime, end_utc: datetime) -> list[Event]:
        stmt = (
            select(Event)
            .where(
                Event.user_id == user_id,
                Event.is_active.is_(True),
                Event.starts_at >= start_utc,
                Event.starts_at < end_utc,
            )
            .order_by(Event.starts_at)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())
