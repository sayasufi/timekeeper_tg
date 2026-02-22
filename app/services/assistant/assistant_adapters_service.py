from __future__ import annotations

from app.domain.commands import ParsedCommand
from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.batch_execution_service import BatchExecutionService
from app.services.assistant.command_execution_service import CommandExecutionService
from app.services.assistant.response_orchestration_service import ResponseOrchestrationService
from app.services.parser.command_parser_service import CommandParserService


class AssistantAdaptersService:
    def __init__(self, *, parser: CommandParserService) -> None:
        self._parser = parser
        self._command_execution: CommandExecutionService | None = None
        self._batch_execution: BatchExecutionService | None = None
        self._response_orchestration: ResponseOrchestrationService | None = None

    def bind_services(
        self,
        *,
        command_execution: CommandExecutionService,
        batch_execution: BatchExecutionService,
        response_orchestration: ResponseOrchestrationService,
    ) -> None:
        self._command_execution = command_execution
        self._batch_execution = batch_execution
        self._response_orchestration = response_orchestration

    async def execute_with_disambiguation(
        self,
        user: object,
        command: ParsedCommand,
    ) -> AssistantResponse:
        if self._command_execution is None:
            raise RuntimeError("command_execution is not bound")
        return await self._command_execution.execute_with_disambiguation(
            user=user,
            command=command,
        )

    async def finalize_response(
        self,
        user: object,
        source_text: str | None,
        response: AssistantResponse,
    ) -> AssistantResponse:
        if self._response_orchestration is None:
            raise RuntimeError("response_orchestration is not bound")
        return await self._response_orchestration.finalize_response(
            user=user,
            source_text=source_text,
            response=response,
        )

    async def handle_batch_operations(
        self,
        user: object,
        operations: list[str],
        user_memory: object,
        execution_strategy: str = "partial_commit",
        stop_on_error: bool = False,
    ) -> AssistantResponse:
        if self._batch_execution is None:
            raise RuntimeError("batch_execution is not bound")
        return await self._batch_execution.execute_operations(
            user=user,
            user_memory=user_memory,
            operations=operations,
            execution_strategy=execution_strategy,
            stop_on_error=stop_on_error,
        )

    async def execute_batch_command(
        self,
        user: object,
        command: ParsedCommand,
    ) -> AssistantResponse:
        return await self.execute_with_disambiguation(
            user=user,
            command=command,
        )

    async def execute_batch_with_args(
        self,
        user: object,
        operations: list[str],
        user_memory: object,
        execution_strategy: str,
        stop_on_error: bool,
    ) -> AssistantResponse:
        return await self.handle_batch_operations(
            user=user,
            operations=operations,
            user_memory=user_memory,
            execution_strategy=execution_strategy,
            stop_on_error=stop_on_error,
        )

    async def ask_clarification(
        self,
        *,
        user: object,
        source_text: str,
        reason: str,
        fallback: str,
        user_memory: object,
        context: dict[str, object] | None = None,
    ) -> str:
        from app.db.models import User
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user, User):
            return fallback
        if not isinstance(user_memory, UserMemoryProfile):
            return fallback
        return await self._parser.generate_clarification(
            text=source_text,
            reason=reason,
            locale=user.language,
            timezone=user.timezone,
            fallback=fallback,
            user_memory=user_memory,
            context=context,
        )

