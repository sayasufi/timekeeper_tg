from __future__ import annotations

from typing import Any
from uuid import UUID

from app.domain.commands import UpdateScheduleCommand
from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.assistant_use_cases_service import AssistantUseCasesService
from app.services.assistant.command_execution_service import CommandExecutionService


class AssistantService:
    def __init__(
        self,
        *,
        use_cases: AssistantUseCasesService,
        command_execution: CommandExecutionService,
    ) -> None:
        self._use_cases = use_cases
        self._command_execution = command_execution

    async def handle_text(self, telegram_id: int, text: str, language: str) -> AssistantResponse:
        return await self._use_cases.handle_text(
            telegram_id=telegram_id,
            text=text,
            language=language,
        )

    async def handle_resolution(
        self,
        telegram_id: int,
        language: str,
        command_payload: dict[str, Any],
        selected_event_id: UUID,
    ) -> AssistantResponse:
        return await self._use_cases.handle_resolution(
            telegram_id=telegram_id,
            language=language,
            command_payload=command_payload,
            selected_event_id=selected_event_id,
        )

    async def handle_confirmation(
        self,
        telegram_id: int,
        language: str,
        command_payload: dict[str, Any],
        event_id: UUID | None,
        confirmed: bool,
        confirmation_action: str | None = None,
    ) -> AssistantResponse:
        return await self._use_cases.handle_confirmation(
            telegram_id=telegram_id,
            language=language,
            command_payload=command_payload,
            event_id=event_id,
            confirmed=confirmed,
            confirmation_action=confirmation_action,
        )

    async def handle_pending_reschedule(
        self,
        telegram_id: int,
        language: str,
        event_id: UUID,
        text: str,
    ) -> AssistantResponse:
        return await self._use_cases.handle_pending_reschedule(
            telegram_id=telegram_id,
            language=language,
            event_id=event_id,
            text=text,
        )

    async def handle_quick_action(
        self,
        telegram_id: int,
        language: str,
        action: str,
        payload: dict[str, Any],
    ) -> AssistantResponse:
        return await self._use_cases.handle_quick_action(
            telegram_id=telegram_id,
            language=language,
            action=action,
            payload=payload,
        )

    def _needs_schedule_scope_clarification(self, command: UpdateScheduleCommand) -> bool:
        return self._command_execution.needs_schedule_scope_clarification(command)

