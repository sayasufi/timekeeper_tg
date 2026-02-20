from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import OutboxMessage


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        user_id: int,
        payload: dict[str, object],
        available_at: datetime,
        dedupe_key: str | None = None,
        channel: str = "telegram",
    ) -> OutboxMessage:
        if dedupe_key:
            existing = await self.get_by_dedupe_key(dedupe_key)
            if existing is not None:
                return existing

        item = OutboxMessage(
            user_id=user_id,
            channel=channel,
            payload=payload,
            status="pending",
            attempts=0,
            available_at=available_at,
            dedupe_key=dedupe_key,
            last_error=None,
        )
        self._session.add(item)
        await self._session.flush()
        return item

    async def get_by_dedupe_key(self, dedupe_key: str) -> OutboxMessage | None:
        stmt = select(OutboxMessage).where(OutboxMessage.dedupe_key == dedupe_key)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_ready(self, now_utc: datetime, limit: int = 200) -> list[OutboxMessage]:
        stmt = (
            select(OutboxMessage)
            .where(OutboxMessage.status == "pending", OutboxMessage.available_at <= now_utc)
            .order_by(OutboxMessage.available_at)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def get_by_id(self, outbox_id: UUID) -> OutboxMessage | None:
        stmt = select(OutboxMessage).where(OutboxMessage.id == outbox_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_sent(self, item: OutboxMessage) -> None:
        item.status = "sent"
        item.last_error = None
        await self._session.flush()

    async def postpone(self, item: OutboxMessage, next_time: datetime) -> None:
        item.available_at = next_time
        item.status = "pending"
        await self._session.flush()

    async def mark_failed(self, item: OutboxMessage, error: str) -> None:
        item.status = "failed"
        item.last_error = error
        await self._session.flush()

    async def mark_dead_letter(self, item: OutboxMessage, error: str) -> None:
        item.status = "dead_letter"
        item.last_error = error
        await self._session.flush()

    async def inc_attempts(self, item: OutboxMessage) -> None:
        item.attempts += 1
        await self._session.flush()

    async def requeue(self, item: OutboxMessage, available_at: datetime) -> None:
        item.status = "pending"
        item.available_at = available_at
        await self._session.flush()
