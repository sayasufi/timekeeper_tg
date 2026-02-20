from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_repository import UserRepository


@pytest.mark.asyncio
async def test_get_or_create_defaults_timezone_ru(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.get_or_create(telegram_id=9001, language="ru")
    await db_session.commit()
    assert user.timezone == "Europe/Moscow"


@pytest.mark.asyncio
async def test_get_or_create_defaults_timezone_kk(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.get_or_create(telegram_id=9002, language="kk")
    await db_session.commit()
    assert user.timezone == "Asia/Almaty"


@pytest.mark.asyncio
async def test_get_or_create_defaults_timezone_other_to_utc(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await repo.get_or_create(telegram_id=9003, language="en")
    await db_session.commit()
    assert user.timezone == "UTC"
