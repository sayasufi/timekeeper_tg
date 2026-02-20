from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.datetime_utils import is_local_time_in_range, parse_hhmm
from app.db.models import User
from app.integrations.telegram.base import Notifier
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.user_repository import UserRepository


class OutboxDeliveryService:
    def __init__(
        self,
        outbox_repository: OutboxRepository,
        user_repository: UserRepository,
        notifier: Notifier,
    ) -> None:
        self._outbox = outbox_repository
        self._users = user_repository
        self._notifier = notifier

    async def deliver_ready(self, now_utc: datetime, limit: int = 200) -> int:
        sent = 0
        items = await self._outbox.list_ready(now_utc, limit=limit)
        for item in items:
            user = await self._users.get_by_id(item.user_id)
            if user is None:
                await self._outbox.mark_failed(item, "user_not_found")
                continue

            postpone_until = self._next_allowed_time(user=user, now_utc=now_utc)
            if postpone_until is not None:
                await self._outbox.postpone(item, postpone_until)
                continue

            await self._outbox.inc_attempts(item)
            try:
                payload = item.payload
                text = str(payload.get("text", ""))
                telegram_id = int(payload.get("telegram_id", user.telegram_id))
                raw_buttons = payload.get("buttons")
                buttons: list[tuple[str, str]] | None = None
                if isinstance(raw_buttons, list):
                    buttons = []
                    for candidate in raw_buttons:
                        if isinstance(candidate, dict):
                            title = candidate.get("title")
                            callback_data = candidate.get("callback_data")
                            if isinstance(title, str) and isinstance(callback_data, str):
                                buttons.append((title, callback_data))
                await self._notifier.send_message(telegram_id, text, buttons=buttons)
                await self._outbox.mark_sent(item)
                sent += 1
            except Exception as exc:
                await self._outbox.mark_failed(item, str(exc))
        return sent

    def _next_allowed_time(self, user: User, now_utc: datetime) -> datetime | None:
        local_now = now_utc.astimezone(ZoneInfo(user.timezone))
        local_time = local_now.time()

        if user.quiet_hours_start and user.quiet_hours_end:
            quiet_start = parse_hhmm(user.quiet_hours_start)
            quiet_end = parse_hhmm(user.quiet_hours_end)
            if is_local_time_in_range(local_time, quiet_start, quiet_end):
                candidate_day = local_now.date()
                if quiet_start > quiet_end and local_time >= quiet_start:
                    candidate_day = candidate_day + timedelta(days=1)
                next_local = datetime.combine(candidate_day, quiet_end, tzinfo=local_now.tzinfo)
                return next_local.astimezone(UTC)

        if user.work_hours_start and user.work_hours_end:
            work_start = parse_hhmm(user.work_hours_start)
            work_end = parse_hhmm(user.work_hours_end)
            if not is_local_time_in_range(local_time, work_start, work_end):
                candidate_day = local_now.date()
                if local_time >= work_end:
                    candidate_day = candidate_day + timedelta(days=1)
                next_local = datetime.combine(candidate_day, work_start, tzinfo=local_now.tzinfo)
                return next_local.astimezone(UTC)

        if user.work_days:
            iso_day = local_now.isoweekday()
            if iso_day not in user.work_days:
                # postpone to next allowed day at 09:00 local
                for offset in range(1, 8):
                    next_day = local_now + timedelta(days=offset)
                    if next_day.isoweekday() in user.work_days:
                        next_local = datetime.combine(
                            next_day.date(),
                            parse_hhmm(user.work_hours_start or "09:00"),
                            tzinfo=local_now.tzinfo,
                        )
                        return next_local.astimezone(UTC)

        return None
