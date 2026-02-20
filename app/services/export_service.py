from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import orjson

from app.repositories.user_repository import UserRepository
from app.services.event_service import EventService


class ExportService:
    def __init__(self, user_repository: UserRepository, event_service: EventService, export_dir: Path) -> None:
        self._users = user_repository
        self._events = event_service
        self._export_dir = export_dir

    async def export_user(self, telegram_id: int) -> tuple[Path, dict[str, object]]:
        user = await self._users.get_by_telegram_id(telegram_id)
        if user is None:
            msg = f"User {telegram_id} not found"
            raise ValueError(msg)

        events = await self._events.serialize_user_events(user.id)
        notes = await self._events.serialize_user_notes(user.id)
        students = await self._events.serialize_user_students(user.id)
        payments = await self._events.serialize_user_payments(user.id)
        payload: dict[str, object] = {
            "exported_at": datetime.now(tz=UTC).isoformat(),
            "user": {
                "id": user.id,
                "telegram_id": user.telegram_id,
                "language": user.language,
                "timezone": user.timezone,
                "quiet_hours_start": user.quiet_hours_start,
                "quiet_hours_end": user.quiet_hours_end,
                "work_hours_start": user.work_hours_start,
                "work_hours_end": user.work_hours_end,
                "work_days": user.work_days,
            },
            "events": events,
            "notes": notes,
            "students": students,
            "payments": payments,
        }

        self._export_dir.mkdir(parents=True, exist_ok=True)
        filename = f"user_{telegram_id}_{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
        path = self._export_dir / filename
        path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
        return path, payload
