from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PaymentTransaction


class PaymentTransactionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, item: PaymentTransaction) -> PaymentTransaction:
        self._session.add(item)
        await self._session.flush()
        return item

    async def list_for_user(
        self,
        user_id: int,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        limit: int = 200,
    ) -> list[PaymentTransaction]:
        stmt = select(PaymentTransaction).where(PaymentTransaction.user_id == user_id)
        if from_utc is not None:
            stmt = stmt.where(PaymentTransaction.created_at >= from_utc)
        if to_utc is not None:
            stmt = stmt.where(PaymentTransaction.created_at < to_utc)
        result = await self._session.execute(stmt.order_by(PaymentTransaction.created_at.desc()).limit(limit))
        return list(result.scalars())
