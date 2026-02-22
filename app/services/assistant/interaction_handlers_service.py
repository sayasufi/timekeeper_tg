from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.commands import DeleteReminderCommand, UpdateReminderCommand, UpdateScheduleCommand
from app.repositories.user_repository import UserRepository
from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.confirmation_service import ConfirmationService
from app.services.assistant.conversation_state_service import ConversationStateService
from app.services.assistant.pending_reschedule_service import PendingRescheduleService
from app.services.assistant.quick_action_service import QuickActionService
from app.services.parser.command_parser_service import CommandParserService
from app.services.smart_agents import UserMemoryAgent

FinalizeFn = Callable[[object, str | None, AssistantResponse], Awaitable[AssistantResponse]]
ExecuteFn = Callable[[object, object], Awaitable[AssistantResponse]]
ExecuteBatchFn = Callable[[object, list[str], object, str, bool], Awaitable[AssistantResponse]]
HandleTextFn = Callable[[int, str, str], Awaitable[AssistantResponse]]


class InteractionHandlersService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        users: UserRepository,
        parser: CommandParserService,
        confirmation_service: ConfirmationService,
        memory: UserMemoryAgent,
        conversation_state: ConversationStateService,
        pending_reschedule: PendingRescheduleService,
        quick_actions: QuickActionService,
        finalize_response: FinalizeFn,
        execute_with_disambiguation: ExecuteFn,
        execute_batch_with_args: ExecuteBatchFn,
        handle_text: HandleTextFn,
    ) -> None:
        self._session = session
        self._users = users
        self._parser = parser
        self._confirmation_service = confirmation_service
        self._memory = memory
        self._conversation_state = conversation_state
        self._pending_reschedule = pending_reschedule
        self._quick_actions = quick_actions
        self._finalize_response = finalize_response
        self._execute_with_disambiguation = execute_with_disambiguation
        self._execute_batch_with_args = execute_batch_with_args
        self._handle_text = handle_text

    async def handle_resolution(
        self,
        *,
        telegram_id: int,
        language: str,
        command_payload: dict[str, Any],
        selected_event_id: UUID,
    ) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        command = self._parser.parse_payload(command_payload)

        if isinstance(command, UpdateReminderCommand | DeleteReminderCommand | UpdateScheduleCommand):
            command.event_id = selected_event_id
            command.search_text = None

        response = await self._execute_with_disambiguation(user, command)
        await self._session.commit()
        return await self._finalize_response(user, None, response)

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
        if not confirmed:
            user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
            response = AssistantResponse("Операция отменена.")
            return await self._finalize_response(user, None, response)

        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        if self._confirmation_service.is_batch_confirmation(confirmation_action, command_payload):
            result = await self._confirmation_service.run_batch_confirmation(
                user=user,
                user_memory=self._memory.build_profile(user),
                command_payload=command_payload,
                execute_batch=self._execute_batch_with_args,
            )
            await self._session.commit()
            return await self._finalize_response(user, None, result)

        command = self._parser.parse_payload(command_payload)
        if event_id is not None and isinstance(command, UpdateReminderCommand | DeleteReminderCommand | UpdateScheduleCommand):
            command.event_id = event_id
            command.search_text = None

        response = await self._execute_with_disambiguation(user, command)
        await self._session.commit()
        return await self._finalize_response(user, None, response)

    async def handle_pending_reschedule(
        self,
        *,
        telegram_id: int,
        language: str,
        event_id: UUID,
        text: str,
    ) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        user_memory = self._memory.build_profile(user)
        context_package = await self._conversation_state.build_context_package(
            user=user,
            state=await self._conversation_state.get_state(telegram_id),
            latest_text=text,
        )
        response, should_commit = await self._pending_reschedule.handle(
            user=user,
            event_id=event_id,
            text=text,
            user_memory=user_memory,
            context_package=context_package,
        )
        if should_commit:
            await self._session.commit()
        return await self._finalize_response(user, text, response)

    async def handle_quick_action(
        self,
        *,
        telegram_id: int,
        language: str,
        action: str,
        payload: dict[str, Any],
    ) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        outcome = await self._quick_actions.handle(
            user=user,
            action=action,
            payload=payload,
        )
        if outcome.delegate_text is not None:
            return await self._handle_text(telegram_id, outcome.delegate_text, language)
        if outcome.should_commit:
            await self._session.commit()
        return await self._finalize_response(user, None, outcome.response)

