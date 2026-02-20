from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import structlog
from redis.asyncio import Redis

from app.db.models import User
from app.domain.enums import EventType
from app.integrations.telegram.base import Notifier
from app.repositories.due_notification_repository import DueNotificationRepository
from app.repositories.event_repository import EventRepository
from app.repositories.notification_log_repository import NotificationLogRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.user_repository import UserRepository
from app.services.bot_response_service import BotResponseService
from app.services.due_index_service import DueIndexService
from app.services.event_service import EventService
from app.services.outbox_delivery_service import OutboxDeliveryService
from app.services.smart_agents import DigestPrioritizationAgent, SummaryAgent

logger = structlog.get_logger(__name__)


class ReminderDispatchService:
    def __init__(
        self,
        user_repository: UserRepository,
        event_repository: EventRepository,
        due_repository: DueNotificationRepository,
        outbox_repository: OutboxRepository,
        log_repository: NotificationLogRepository,
        due_index_service: DueIndexService,
        event_service: EventService,
        notifier: Notifier,
        response_renderer: BotResponseService | None = None,
        redis: Redis | None = None,
        outbox_max_attempts: int = 5,
        outbox_backoff_base_seconds: int = 30,
        outbox_backoff_max_seconds: int = 1800,
        outbox_dedupe_ttl_seconds: int = 86400,
    ) -> None:
        self._users = user_repository
        self._events = event_repository
        self._due = due_repository
        self._outbox = outbox_repository
        self._logs = log_repository
        self._due_index = due_index_service
        self._event_service = event_service
        self._delivery = OutboxDeliveryService(
            outbox_repository,
            user_repository,
            notifier,
            redis=redis,
            max_attempts=outbox_max_attempts,
            backoff_base_seconds=outbox_backoff_base_seconds,
            backoff_max_seconds=outbox_backoff_max_seconds,
            dedupe_ttl_seconds=outbox_dedupe_ttl_seconds,
        )
        self._summary = SummaryAgent(DigestPrioritizationAgent())
        self._renderer = response_renderer

    async def dispatch_due(self, now_utc: datetime, window_seconds: int = 60) -> int:
        enqueued = 0
        due_items = await self._due.list_due(now_utc + timedelta(seconds=window_seconds))

        for item in due_items:
            await self._due.mark_processing(item)
            user = await self._users.get_by_id(item.user_id)
            event = await self._events.get_by_id(item.event_id)
            if user is None or event is None or not event.is_active:
                await self._due.mark_done(item)
                continue

            is_new = await self._logs.mark_sent(
                user_id=user.id,
                event_id=event.id,
                occurrence_at=item.occurrence_at,
                offset_minutes=item.offset_minutes,
            )
            if is_new:
                text = self._format_reminder(user, event.title, item.occurrence_at, item.offset_minutes)
                text = await self._render_for_user(user, text, response_kind="reminder_notification")
                dedupe_key = f"{event.id}:{item.occurrence_at.isoformat()}:{item.offset_minutes}"
                if event.event_type == EventType.LESSON.value:
                    buttons = [
                        {
                            "title": await self._render_button_label(user, "Оплачено"),
                            "callback_data": f"lesson:paid:{event.id}",
                        },
                        {
                            "title": await self._render_button_label(user, "Перенести"),
                            "callback_data": f"lesson:reschedule:{event.id}",
                        },
                        {
                            "title": await self._render_button_label(user, "Пропуск"),
                            "callback_data": f"lesson:missed:{event.id}",
                        },
                        {
                            "title": await self._render_button_label(user, "Заметка"),
                            "callback_data": f"lesson:note:{event.id}",
                        },
                    ]
                else:
                    buttons = []
                await self._outbox.enqueue(
                    user_id=user.id,
                    payload={"telegram_id": user.telegram_id, "text": text, "buttons": buttons},
                    available_at=now_utc,
                    dedupe_key=dedupe_key,
                )
                enqueued += 1

            await self._due_index.advance_after_dispatch(
                event=event,
                offset_minutes=item.offset_minutes,
                current_occurrence=item.occurrence_at,
            )

        logger.info("dispatch_due.completed", enqueued=enqueued)
        return enqueued

    async def deliver_outbox(self, now_utc: datetime, limit: int = 200) -> int:
        sent = await self._delivery.deliver_ready(now_utc=now_utc, limit=limit)
        logger.info("outbox.deliver.completed", sent=sent)
        return sent

    async def send_daily_lesson_digest(self, now_utc: datetime) -> int:
        users = await self._users.list_all()
        enqueued = 0
        for user in users:
            local_now = now_utc.astimezone(ZoneInfo(user.timezone))
            if local_now.hour != 7 or local_now.minute >= 10:
                continue

            lessons = await self._event_service.lessons_for_day(user=user, day=local_now.date())
            if not lessons:
                continue

            lines: list[str] = []
            for occ, event in lessons:
                local = occ.astimezone(ZoneInfo(user.timezone)).strftime("%H:%M")
                lines.append(f"урок {local} {event.title}")
                lesson_buttons = [
                    {
                        "title": await self._render_button_label(user, "Перенести"),
                        "callback_data": f"lesson:reschedule:{event.id}",
                    },
                    {
                        "title": await self._render_button_label(user, "Отменить"),
                        "callback_data": f"lesson:cancel:{event.id}",
                    },
                    {
                        "title": await self._render_button_label(user, "Оплачено"),
                        "callback_data": f"lesson:paid:{event.id}",
                    },
                    {
                        "title": await self._render_button_label(user, "Пропуск"),
                        "callback_data": f"lesson:missed:{event.id}",
                    },
                    {
                        "title": await self._render_button_label(user, "Добавить заметку"),
                        "callback_data": f"lesson:note:{event.id}",
                    },
                ]
                dedupe_key = f"digest_lesson:{user.id}:{event.id}:{local_now.date().isoformat()}"
                await self._outbox.enqueue(
                    user_id=user.id,
                    payload={
                        "telegram_id": user.telegram_id,
                        "text": await self._render_for_user(
                            user,
                            f"Сегодня урок: {local} • {event.title}",
                            response_kind="daily_lesson_item",
                        ),
                        "buttons": lesson_buttons,
                    },
                    available_at=now_utc,
                    dedupe_key=dedupe_key,
                )
                enqueued += 1

            dedupe_key = f"digest_summary:{user.id}:{local_now.date().isoformat()}"
            digest_text = await self._render_for_user(
                user,
                self._summary.summarize(lines),
                response_kind="daily_digest_summary",
            )
            await self._outbox.enqueue(
                user_id=user.id,
                payload={"telegram_id": user.telegram_id, "text": digest_text},
                available_at=now_utc,
                dedupe_key=dedupe_key,
            )
            enqueued += 1

        logger.info("dispatch_daily_digest.completed", enqueued=enqueued)
        return enqueued

    async def send_payment_due_reminders(self, now_utc: datetime) -> int:
        users = await self._users.list_all()
        enqueued = 0
        for user in users:
            local_now = now_utc.astimezone(ZoneInfo(user.timezone))
            if local_now.hour != 20 or local_now.minute >= 10:
                continue
            lessons = await self._event_service.lessons_for_day(user=user, day=local_now.date())
            for occ, event in lessons:
                local_occ = occ.astimezone(ZoneInfo(user.timezone))
                if local_occ >= local_now:
                    continue
                if str(event.extra_data.get("payment_status", "unknown")) == "paid":
                    continue
                text = await self._render_for_user(
                    user,
                    f"Напоминание об оплате: урок {event.title} в {local_occ.strftime('%H:%M')} еще не отмечен как оплаченный.",
                    response_kind="payment_due_reminder",
                )
                dedupe_key = f"payment_due:{user.id}:{event.id}:{local_now.date().isoformat()}"
                await self._outbox.enqueue(
                    user_id=user.id,
                    payload={"telegram_id": user.telegram_id, "text": text},
                    available_at=now_utc,
                    dedupe_key=dedupe_key,
                )
                enqueued += 1
        logger.info("dispatch_payment_due.completed", enqueued=enqueued)
        return enqueued

    async def send_operational_digest(self, now_utc: datetime) -> int:
        users = await self._users.list_all()
        enqueued = 0
        for user in users:
            local_now = now_utc.astimezone(ZoneInfo(user.timezone))
            if local_now.hour not in {7, 20} or local_now.minute >= 10:
                continue
            text = await self._event_service.operational_digest(user=user, now_utc=now_utc)
            text = await self._render_for_user(user, text, response_kind="operational_digest")
            slot = "morning" if local_now.hour == 7 else "evening"
            dedupe_key = f"ops_digest:{slot}:{user.id}:{local_now.date().isoformat()}"
            await self._outbox.enqueue(
                user_id=user.id,
                payload={"telegram_id": user.telegram_id, "text": text},
                available_at=now_utc,
                dedupe_key=dedupe_key,
            )
            enqueued += 1
        logger.info("dispatch_operational_digest.completed", enqueued=enqueued)
        return enqueued

    def _format_reminder(self, user: User, title: str, occurrence_utc: datetime, offset_minutes: int) -> str:
        local = occurrence_utc.astimezone(ZoneInfo(user.timezone)).strftime("%d.%m %H:%M")
        safe_title = escape(title)
        if offset_minutes == 0:
            return (
                f"🔔 <b>Время пришло</b>\n\n"
                f"<b>{safe_title}</b>\n"
                f"📅 {local}"
            )
        return (
            f"⏰ <b>Напоминание через {offset_minutes} мин</b>\n\n"
            f"<b>{safe_title}</b>\n"
            f"📅 Событие: {local}"
        )

    async def _render_for_user(self, user: User, text: str, response_kind: str) -> str:
        if self._renderer is None:
            return text
        return await self._renderer.render_for_user(
            user=user,
            raw_text=text,
            response_kind=response_kind,
            user_text=None,
        )

    async def _render_button_label(self, user: User, label: str) -> str:
        return await self._render_for_user(user, label, response_kind="button_label")
