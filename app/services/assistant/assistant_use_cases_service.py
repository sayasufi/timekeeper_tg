from __future__ import annotations

from typing import Any
from uuid import UUID

from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.conversation_flow_service import ConversationFlowService
from app.services.assistant.interaction_handlers_service import InteractionHandlersService


class AssistantUseCasesService:
    def __init__(
        self,
        *,
        flow: ConversationFlowService,
        interactions: InteractionHandlersService,
    ) -> None:
        self._flow = flow
        self._interactions = interactions

    async def handle_text(self, *, telegram_id: int, text: str, language: str) -> AssistantResponse:
        return await self._flow.handle_text(
            telegram_id=telegram_id,
            text=text,
            language=language,
        )

    async def handle_resolution(
        self,
        *,
        telegram_id: int,
        language: str,
        command_payload: dict[str, Any],
        selected_event_id: UUID,
    ) -> AssistantResponse:
        return await self._interactions.handle_resolution(
            telegram_id=telegram_id,
            language=language,
            command_payload=command_payload,
            selected_event_id=selected_event_id,
        )

    async def handle_confirmation(
        self,
        *,
        telegram_id: int,
        language: str,
        command_payload: dict[str, Any],
        event_id: UUID | None,
        confirmed: bool,
        confirmation_action: str | None = None,
    ) -> AssistantResponse:
        return await self._interactions.handle_confirmation(
            telegram_id=telegram_id,
            language=language,
            command_payload=command_payload,
            event_id=event_id,
            confirmed=confirmed,
            confirmation_action=confirmation_action,
        )

    async def handle_pending_reschedule(
        self,
        *,
        telegram_id: int,
        language: str,
        event_id: UUID,
        text: str,
    ) -> AssistantResponse:
        return await self._interactions.handle_pending_reschedule(
            telegram_id=telegram_id,
            language=language,
            event_id=event_id,
            text=text,
        )

    async def handle_quick_action(
        self,
        *,
        telegram_id: int,
        language: str,
        action: str,
        payload: dict[str, Any],
    ) -> AssistantResponse:
        return await self._interactions.handle_quick_action(
            telegram_id=telegram_id,
            language=language,
            action=action,
            payload=payload,
        )

