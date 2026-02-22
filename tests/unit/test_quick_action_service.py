from __future__ import annotations

from typing import cast

import pytest

from app.db.models import User
from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.quick_action_service import QuickActionService


class _FakeEvents:
    async def create_note(self, user: User, command: object) -> str:
        return "note_created"


class _FakePendingReschedule:
    async def quick_pick(self, *, user: object, payload: dict[str, object]) -> tuple[AssistantResponse, bool]:
        return AssistantResponse("rescheduled"), True


@pytest.mark.asyncio
async def test_quick_action_service_send_text_choice_returns_delegate() -> None:
    service = QuickActionService(
        events=cast(object, _FakeEvents()),
        pending_reschedule=cast(object, _FakePendingReschedule()),
    )

    outcome = await service.handle(
        user=User(telegram_id=1, language="ru", timezone="UTC"),
        action="send_text_choice",
        payload={"text": "покажи сегодня"},
    )

    assert outcome.delegate_text == "покажи сегодня"
    assert outcome.should_commit is False


@pytest.mark.asyncio
async def test_quick_action_service_reschedule_pick_commit() -> None:
    service = QuickActionService(
        events=cast(object, _FakeEvents()),
        pending_reschedule=cast(object, _FakePendingReschedule()),
    )

    outcome = await service.handle(
        user=User(telegram_id=1, language="ru", timezone="UTC"),
        action="reschedule_pick",
        payload={"event_id": "x"},
    )

    assert outcome.response.text == "rescheduled"
    assert outcome.should_commit is True

