from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.outbox_repository import OutboxRepository
from app.repositories.user_repository import UserRepository
from app.services.reminders.outbox_delivery_service import OutboxDeliveryService


class FlakyNotifier:
    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self.calls = 0

    async def send_message(
        self,
        telegram_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RuntimeError("temporary")


class ProbeNotifier:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(
        self,
        telegram_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_outbox_retry_with_backoff_then_success(
    db_session: AsyncSession,
    fake_redis: object,
) -> None:
    users = UserRepository(db_session)
    outbox = OutboxRepository(db_session)
    user = await users.get_or_create(telegram_id=300, language="ru")
    now = datetime(2026, 2, 20, 10, 0, tzinfo=UTC)
    item = await outbox.enqueue(
        user_id=user.id,
        payload={"telegram_id": user.telegram_id, "text": "hi"},
        available_at=now,
        dedupe_key="r1",
    )
    notifier = FlakyNotifier(fail_times=1)
    service = OutboxDeliveryService(
        outbox,
        users,
        notifier,
        redis=fake_redis,  # type: ignore[arg-type]
        max_attempts=3,
        backoff_base_seconds=1,
        backoff_max_seconds=10,
    )

    sent_first = await service.deliver_ready(now)
    assert sent_first == 0
    updated = await outbox.get_by_id(item.id)
    assert updated is not None
    assert updated.status == "pending"
    assert updated.attempts == 1
    assert updated.available_at == now + timedelta(seconds=1)

    sent_second = await service.deliver_ready(now + timedelta(seconds=1))
    assert sent_second == 1
    updated2 = await outbox.get_by_id(item.id)
    assert updated2 is not None
    assert updated2.status == "sent"
    assert notifier.calls == 2


@pytest.mark.asyncio
async def test_outbox_moves_to_dead_letter_after_max_attempts(
    db_session: AsyncSession,
    fake_redis: object,
) -> None:
    users = UserRepository(db_session)
    outbox = OutboxRepository(db_session)
    user = await users.get_or_create(telegram_id=301, language="ru")
    now = datetime(2026, 2, 20, 10, 0, tzinfo=UTC)
    item = await outbox.enqueue(
        user_id=user.id,
        payload={"telegram_id": user.telegram_id, "text": "boom"},
        available_at=now,
        dedupe_key="r2",
    )
    notifier = FlakyNotifier(fail_times=10)
    service = OutboxDeliveryService(
        outbox,
        users,
        notifier,
        redis=fake_redis,  # type: ignore[arg-type]
        max_attempts=2,
        backoff_base_seconds=1,
        backoff_max_seconds=10,
    )

    await service.deliver_ready(now)
    await service.deliver_ready(now + timedelta(seconds=1))
    updated = await outbox.get_by_id(item.id)
    assert updated is not None
    assert updated.status == "dead_letter"
    assert updated.attempts == 2


@pytest.mark.asyncio
async def test_outbox_redis_dedupe_marks_sent_without_second_send(
    db_session: AsyncSession,
    fake_redis: object,
) -> None:
    users = UserRepository(db_session)
    outbox = OutboxRepository(db_session)
    user = await users.get_or_create(telegram_id=302, language="ru")
    now = datetime(2026, 2, 20, 10, 0, tzinfo=UTC)
    item = await outbox.enqueue(
        user_id=user.id,
        payload={"telegram_id": user.telegram_id, "text": "once"},
        available_at=now,
        dedupe_key="r3",
    )
    redis = fake_redis  # type: ignore[assignment]
    await redis.set(f"outbox:delivered:{item.id.hex}", "1", ex=3600)
    notifier = ProbeNotifier()
    service = OutboxDeliveryService(
        outbox,
        users,
        notifier,
        redis=redis,  # type: ignore[arg-type]
    )

    sent = await service.deliver_ready(now)
    assert sent == 1
    updated = await outbox.get_by_id(item.id)
    assert updated is not None
    assert updated.status == "sent"
    assert notifier.calls == 0

