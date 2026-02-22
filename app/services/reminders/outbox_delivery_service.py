from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from redis.asyncio import Redis

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
        redis: Redis | None = None,
        max_attempts: int = 5,
        backoff_base_seconds: int = 30,
        backoff_max_seconds: int = 1800,
        dedupe_ttl_seconds: int = 86400,
    ) -> None:
        self._outbox = outbox_repository
        self._users = user_repository
        self._notifier = notifier
        self._redis = redis
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = max(1, backoff_base_seconds)
        self._backoff_max = max(self._backoff_base, backoff_max_seconds)
        self._dedupe_ttl = max(60, dedupe_ttl_seconds)

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
                if await self._was_delivered(item.id.hex):
                    await self._outbox.mark_sent(item)
                    sent += 1
                    continue
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
                await self._mark_delivered(item.id.hex)
                await self._outbox.mark_sent(item)
                sent += 1
            except Exception as exc:
                if item.attempts >= self._max_attempts:
                    await self._outbox.mark_dead_letter(item, str(exc))
                    continue
                next_time = now_utc + timedelta(seconds=self._backoff_seconds(item.attempts))
                await self._outbox.postpone(item, next_time)
                item.last_error = str(exc)
        return sent

    def _backoff_seconds(self, attempts: int) -> int:
        value = self._backoff_base * (2 ** max(0, attempts - 1))
        return min(value, self._backoff_max)

    async def _was_delivered(self, key_suffix: str) -> bool:
        if self._redis is None:
            return False
        key = f"outbox:delivered:{key_suffix}"
        raw = await self._redis.get(key)
        return raw is not None

    async def _mark_delivered(self, key_suffix: str) -> None:
        if self._redis is None:
            return
        key = f"outbox:delivered:{key_suffix}"
        await self._redis.set(key, "1", ex=self._dedupe_ttl)

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
