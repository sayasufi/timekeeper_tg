from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest

from app.db.models import User
from app.services.assistant.pending_reschedule_service import PendingRescheduleService


class _FakeEvents:
    def __init__(self) -> None:
        self.last_command = None

    async def update_schedule(self, user: User, command: object) -> str:
        self.last_command = command
        return "updated"


async def _ask_clarification(**_: object) -> str:
    return "clarify"


@pytest.mark.asyncio
async def test_pending_reschedule_handle_rejects_non_user() -> None:
    service = PendingRescheduleService(
        parser=cast(object, object()),
        events=cast(object, object()),
        ask_clarification=_ask_clarification,
    )

    response, should_commit = await service.handle(
        user=object(),
        event_id=uuid4(),
        text="перенеси урок",
        user_memory={},
        context_package={},
    )

    assert should_commit is False
    assert "не удалось" in response.text.lower()


@pytest.mark.asyncio
async def test_pending_reschedule_quick_pick_valid_payload() -> None:
    events = _FakeEvents()
    service = PendingRescheduleService(
        parser=cast(object, object()),
        events=cast(object, events),
        ask_clarification=_ask_clarification,
    )
    user = User(telegram_id=1, language="ru", timezone="Europe/Moscow")
    event_id = uuid4()

    response, should_commit = await service.quick_pick(
        user=user,
        payload={
            "event_id": str(event_id),
            "new_date": "2026-03-10",
            "new_time": "18:00",
            "apply_scope": "single_week",
        },
    )

    assert should_commit is True
    assert response.text == "updated"
    assert events.last_command is not None

