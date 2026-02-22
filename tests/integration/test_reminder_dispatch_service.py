from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Event
from app.repositories.due_notification_repository import DueNotificationRepository
from app.repositories.event_repository import EventRepository
from app.repositories.notification_log_repository import NotificationLogRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.user_repository import UserRepository
from app.services.events.event_service import EventService
from app.services.reminders.due_index_service import DueIndexService
from app.services.reminders.reminder_dispatch_service import ReminderDispatchService


class NotifierProbe(Protocol):
    messages: list[tuple[int, str]]

    async def send_message(
        self,
        telegram_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None: ...

    async def close(self) -> None: ...


@pytest.mark.asyncio
async def test_dispatch_due_sends_message(
    db_session: AsyncSession,
    fake_notifier: NotifierProbe,
) -> None:
    users = UserRepository(db_session)
    events = EventRepository(db_session)
    due_repo = DueNotificationRepository(db_session)
    outbox_repo = OutboxRepository(db_session)
    logs = NotificationLogRepository(db_session)
    due_index = DueIndexService(due_repo)
    event_service = EventService(events, due_index_service=due_index)

    user = await users.get_or_create(telegram_id=100, language="ru")
    user.timezone = "UTC"

    event = Event(
        user_id=user.id,
        event_type="reminder",
        title="Оплата",
        starts_at=datetime(2026, 2, 19, 12, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[0],
        extra_data={},
    )
    await events.create(event)
    await due_index.sync_event(event, now_utc=datetime(2026, 2, 19, 11, 0, tzinfo=UTC))
    await db_session.commit()

    service = ReminderDispatchService(users, events, due_repo, outbox_repo, logs, due_index, event_service, fake_notifier)
    enqueued = await service.dispatch_due(datetime(2026, 2, 19, 12, 0, tzinfo=UTC))
    sent = await service.deliver_outbox(datetime(2026, 2, 19, 12, 0, tzinfo=UTC))

    assert enqueued == 1
    assert sent == 1
    assert len(fake_notifier.messages) == 1


@pytest.mark.asyncio
async def test_dispatch_due_deduplicates_same_window(
    db_session: AsyncSession,
    fake_notifier: NotifierProbe,
) -> None:
    users = UserRepository(db_session)
    events = EventRepository(db_session)
    due_repo = DueNotificationRepository(db_session)
    outbox_repo = OutboxRepository(db_session)
    logs = NotificationLogRepository(db_session)
    due_index = DueIndexService(due_repo)
    event_service = EventService(events, due_index_service=due_index)

    user = await users.get_or_create(telegram_id=200, language="ru")
    user.timezone = "UTC"

    event = Event(
        user_id=user.id,
        event_type="reminder",
        title="Call",
        starts_at=datetime(2026, 2, 20, 15, 0, tzinfo=UTC),
        rrule=None,
        remind_offsets=[0],
        extra_data={},
    )
    await events.create(event)
    await due_index.sync_event(event, now_utc=datetime(2026, 2, 20, 14, 0, tzinfo=UTC))
    await db_session.commit()

    service = ReminderDispatchService(users, events, due_repo, outbox_repo, logs, due_index, event_service, fake_notifier)

    first = await service.dispatch_due(datetime(2026, 2, 20, 15, 0, tzinfo=UTC))
    sent_first = await service.deliver_outbox(datetime(2026, 2, 20, 15, 0, tzinfo=UTC))
    await db_session.commit()

    second = await service.dispatch_due(datetime(2026, 2, 20, 15, 0, tzinfo=UTC))
    sent_second = await service.deliver_outbox(datetime(2026, 2, 20, 15, 0, tzinfo=UTC))

    assert first == 1
    assert sent_first == 1
    assert second == 0
    assert sent_second == 0
    assert len(fake_notifier.messages) == 1

