from __future__ import annotations

from typing import cast

from app.domain.commands import UpdateScheduleCommand
from app.domain.enums import Intent
from app.services.assistant.assistant_service import AssistantService
from app.services.assistant.assistant_use_cases_service import AssistantUseCasesService


class _FakeCommandExecution:
    def needs_schedule_scope_clarification(self, command: UpdateScheduleCommand) -> bool:
        if command.apply_scope is not None:
            return False
        return any(
            [
                command.new_date is not None,
                command.new_time is not None,
                command.weekday is not None,
                command.time is not None,
                command.duration_minutes is not None,
                command.delete,
            ]
        )


def _build_service() -> AssistantService:
    use_cases = cast(AssistantUseCasesService, object())
    return AssistantService(
        use_cases=use_cases,
        command_execution=_FakeCommandExecution(),  # type: ignore[arg-type]
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

