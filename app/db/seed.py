from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.models import Event
from app.db.session import create_engine, create_session_factory
from app.domain.enums import EventType
from app.repositories.event_repository import EventRepository
from app.repositories.user_repository import UserRepository


async def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        users = UserRepository(session)
        events = EventRepository(session)

        user = await users.get_or_create(telegram_id=100000001, language="ru")
        user.timezone = "Europe/Moscow"

        existing = await events.list_for_user(user.id, only_active=False)
        if not existing:
            reminder = Event(
                user_id=user.id,
                event_type=EventType.REMINDER.value,
                title="Оплатить интернет",
                starts_at=datetime.now(tz=UTC) + timedelta(hours=2),
                rrule=None,
                remind_offsets=[60, 15, 0],
                extra_data={},
            )
            await events.create(reminder)

        await session.commit()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
