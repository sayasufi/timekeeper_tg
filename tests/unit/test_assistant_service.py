from __future__ import annotations

from typing import cast

from app.domain.commands import UpdateScheduleCommand
from app.domain.enums import Intent
from app.services.assistant_service import AssistantService


def _build_service() -> AssistantService:
    return AssistantService(
        session=cast(object, object()),
        user_repository=cast(object, object()),
        parser_service=cast(object, object()),
        event_service=cast(object, object()),
    )


def test_needs_scope_clarification_for_ambiguous_reschedule() -> None:
    service = _build_service()
    command = UpdateScheduleCommand(
        intent=Intent.UPDATE_SCHEDULE,
        search_text="Маша",
        new_date="2026-03-11",
        new_time="18:00",
        apply_scope=None,
    )

    assert service._needs_schedule_scope_clarification(command) is True


def test_no_scope_clarification_when_scope_is_explicit() -> None:
    service = _build_service()
    command = UpdateScheduleCommand(
        intent=Intent.UPDATE_SCHEDULE,
        search_text="Маша",
        new_date="2026-03-11",
        new_time="18:00",
        apply_scope="single_week",
    )

    assert service._needs_schedule_scope_clarification(command) is False
