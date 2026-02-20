from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import Event
from app.repositories.due_notification_repository import DueNotificationRepository
from app.services.occurrence_service import event_next_occurrence


class DueIndexService:
    def __init__(self, due_repository: DueNotificationRepository) -> None:
        self._due = due_repository

    async def sync_event(self, event: Event, now_utc: datetime | None = None) -> None:
        now = now_utc or datetime.now(tz=UTC)
        await self._due.delete_for_event(event.id)

        if not event.is_active:
            return

        next_occurrence = event_next_occurrence(event, now)
        if next_occurrence is None:
            return

        offsets = event.remind_offsets or [0]
        for offset in offsets:
            trigger_at = next_occurrence - timedelta(minutes=offset)
            await self._due.upsert(
                user_id=event.user_id,
                event_id=event.id,
                occurrence_at=next_occurrence,
                offset_minutes=offset,
                trigger_at=trigger_at,
            )

    async def advance_after_dispatch(
        self,
        event: Event,
        offset_minutes: int,
        current_occurrence: datetime,
    ) -> None:
        item = await self._due.get_by_unique(event.id, current_occurrence, offset_minutes)
        if item is None:
            return

        if not event.is_active:
            await self._due.mark_done(item)
            return

        next_occurrence = event_next_occurrence(event, current_occurrence + timedelta(seconds=1))
        if next_occurrence is None:
            await self._due.mark_done(item)
            return

        next_trigger = next_occurrence - timedelta(minutes=offset_minutes)
        await self._due.mark_pending(item=item, trigger_at=next_trigger, occurrence_at=next_occurrence)
