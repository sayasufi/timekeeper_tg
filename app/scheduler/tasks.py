from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import structlog
from celery import shared_task

from app.core.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.integrations.telegram.notifier import TelegramNotifier
from app.repositories.due_notification_repository import DueNotificationRepository
from app.repositories.event_repository import EventRepository
from app.repositories.notification_log_repository import NotificationLogRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.payment_transaction_repository import PaymentTransactionRepository
from app.repositories.user_repository import UserRepository
from app.services.events.event_service import EventService
from app.services.reminders.due_index_service import DueIndexService
from app.services.reminders.reminder_dispatch_service import ReminderDispatchService

logger = structlog.get_logger(__name__)


async def _dispatch_due_notifications_async() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    notifier = TelegramNotifier(settings.telegram_bot_token)

    try:
        async with session_factory() as session:
            user_repo = UserRepository(session)
            event_repo = EventRepository(session)
            due_repo = DueNotificationRepository(session)
            outbox_repo = OutboxRepository(session)
            log_repo = NotificationLogRepository(session)
            due_index_service = DueIndexService(due_repo)
            payment_repo = PaymentTransactionRepository(session)
            event_service = EventService(
                event_repo,
                due_index_service=due_index_service,
                payment_repository=payment_repo,
            )
            dispatch_service = ReminderDispatchService(
                user_repository=user_repo,
                event_repository=event_repo,
                due_repository=due_repo,
                outbox_repository=outbox_repo,
                log_repository=log_repo,
                due_index_service=due_index_service,
                event_service=event_service,
                notifier=notifier,
            )
            enqueued = await dispatch_service.dispatch_due(
                now_utc=datetime.now(tz=UTC),
                window_seconds=settings.scheduler_poll_seconds,
            )
            sent = await dispatch_service.deliver_outbox(now_utc=datetime.now(tz=UTC))
            await session.commit()
            return enqueued + sent
    finally:
        await notifier.close()
        await engine.dispose()


async def _send_daily_lessons_digest_async() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    notifier = TelegramNotifier(settings.telegram_bot_token)

    try:
        async with session_factory() as session:
            user_repo = UserRepository(session)
            event_repo = EventRepository(session)
            due_repo = DueNotificationRepository(session)
            outbox_repo = OutboxRepository(session)
            log_repo = NotificationLogRepository(session)
            due_index_service = DueIndexService(due_repo)
            payment_repo = PaymentTransactionRepository(session)
            event_service = EventService(
                event_repo,
                due_index_service=due_index_service,
                payment_repository=payment_repo,
            )
            dispatch_service = ReminderDispatchService(
                user_repository=user_repo,
                event_repository=event_repo,
                due_repository=due_repo,
                outbox_repository=outbox_repo,
                log_repository=log_repo,
                due_index_service=due_index_service,
                event_service=event_service,
                notifier=notifier,
            )
            enqueued = await dispatch_service.send_daily_lesson_digest(datetime.now(tz=UTC))
            enqueued += await dispatch_service.send_payment_due_reminders(datetime.now(tz=UTC))
            enqueued += await dispatch_service.send_operational_digest(datetime.now(tz=UTC))
            sent = await dispatch_service.deliver_outbox(now_utc=datetime.now(tz=UTC))
            await session.commit()
            return enqueued + sent
    finally:
        await notifier.close()
        await engine.dispose()


async def _deliver_outbox_async() -> int:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    notifier = TelegramNotifier(settings.telegram_bot_token)

    try:
        async with session_factory() as session:
            user_repo = UserRepository(session)
            outbox_repo = OutboxRepository(session)
            from app.services.reminders.outbox_delivery_service import OutboxDeliveryService

            service = OutboxDeliveryService(outbox_repo, user_repo, notifier)
            sent = await service.deliver_ready(now_utc=datetime.now(tz=UTC))
            await session.commit()
            return sent
    finally:
        await notifier.close()
        await engine.dispose()


@shared_task(name="app.scheduler.tasks.dispatch_due_notifications")  # type: ignore[untyped-decorator]
def dispatch_due_notifications() -> int:
    sent = asyncio.run(_dispatch_due_notifications_async())
    logger.info("task.dispatch_due_notifications", sent=sent)
    return sent


@shared_task(name="app.scheduler.tasks.send_daily_lessons_digest")  # type: ignore[untyped-decorator]
def send_daily_lessons_digest() -> int:
    sent = asyncio.run(_send_daily_lessons_digest_async())
    logger.info("task.send_daily_lessons_digest", sent=sent)
    return sent


@shared_task(name="app.scheduler.tasks.deliver_outbox")  # type: ignore[untyped-decorator]
def deliver_outbox() -> int:
    sent = asyncio.run(_deliver_outbox_async())
    logger.info("task.deliver_outbox", sent=sent)
    return sent

