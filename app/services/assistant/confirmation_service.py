from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.services.assistant.assistant_response import AssistantResponse

BatchExecutorFn = Callable[
    [object, list[str], object, str, bool],
    Awaitable[AssistantResponse],
]


class ConfirmationService:
    def is_batch_confirmation(self, confirmation_action: str | None, command_payload: dict[str, Any]) -> bool:
        return confirmation_action == "batch_execute" or command_payload.get("__kind") == "batch_plan"

    async def run_batch_confirmation(
        self,
        *,
        user: object,
        user_memory: object,
        command_payload: dict[str, Any],
        execute_batch: BatchExecutorFn,
    ) -> AssistantResponse:
        operations_raw = command_payload.get("operations")
        if not isinstance(operations_raw, list):
            return AssistantResponse("Не удалось восстановить пакет операций для подтверждения.")

        operations = [str(item).strip() for item in operations_raw if str(item).strip()]
        if not operations:
            return AssistantResponse("Пакет операций пуст, выполнять нечего.")

        execution_strategy = str(command_payload.get("execution_strategy", "partial_commit"))
        if execution_strategy not in {"all_or_nothing", "partial_commit"}:
            execution_strategy = "partial_commit"
        stop_on_error = bool(command_payload.get("stop_on_error", False))

        return await execute_batch(
            user,
            operations,
            user_memory,
            execution_strategy,
            stop_on_error,
        )
