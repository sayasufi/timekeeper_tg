from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.commands import ClarifyCommand, ParsedCommand
from app.services.assistant.assistant_response import AssistantResponse
from app.services.parser.command_parser_service import CommandParserService

ExecuteCommandFn = Callable[[object, ParsedCommand], Awaitable[AssistantResponse]]
AskClarificationFn = Callable[..., Awaitable[str]]


class BatchExecutionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        parser: CommandParserService,
        execute_command: ExecuteCommandFn,
        ask_clarification: AskClarificationFn,
    ) -> None:
        self._session = session
        self._parser = parser
        self._execute_command = execute_command
        self._ask_clarification = ask_clarification

    async def execute_operations(
        self,
        *,
        user: object,
        user_memory: object,
        operations: list[str],
        execution_strategy: str = "partial_commit",
        stop_on_error: bool = False,
    ) -> AssistantResponse:
        from app.db.models import User
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user_memory, UserMemoryProfile):
            return AssistantResponse("Не удалось обработать пакет операций.")
        if not isinstance(user, User):
            return AssistantResponse("Не удалось обработать пакет операций.")

        executed_texts: list[str] = []
        all_or_nothing = execution_strategy == "all_or_nothing"
        failed = False
        original_text = "\n".join(operations)

        for index, item in enumerate(operations, start=1):
            savepoint = await self._session.begin_nested() if all_or_nothing else None
            response = await self._execute_item(
                user=user,
                user_memory=user_memory,
                original_text=original_text,
                item=item,
            )
            if response is None:
                executed_texts.append(f"{index}. Шаг пропущен.")
                if savepoint is not None:
                    await savepoint.rollback()
                continue
            if response.ambiguity is not None or response.confirmation is not None or response.quick_actions:
                if savepoint is not None:
                    await savepoint.rollback()
                    failed = True
                prefix = "\n".join(executed_texts)
                if prefix:
                    response.text = f"Выполнено:\n{prefix}\n\nСледующий шаг:\n{response.text}"
                return response
            if self._is_failure_response(response.text):
                executed_texts.append(f"{index}. {response.text}")
                if savepoint is not None:
                    await savepoint.rollback()
                    failed = True
                break
            if savepoint is not None:
                await savepoint.commit()
            executed_texts.append(f"{index}. {response.text}")
            if stop_on_error and failed:
                break

        if all_or_nothing and failed:
            return AssistantResponse(
                "Пакет не применен целиком из-за ошибки в одном из шагов.\n"
                + "\n".join(executed_texts)
            )
        return AssistantResponse("Готово:\n" + "\n".join(executed_texts))

    async def _execute_item(
        self,
        *,
        user: object,
        user_memory: object,
        original_text: str,
        item: str,
    ) -> AssistantResponse | None:
        from app.db.models import User
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user_memory, UserMemoryProfile):
            return AssistantResponse("Не удалось обработать шаг.")
        if not isinstance(user, User):
            return AssistantResponse("Не удалось обработать шаг.")

        attempt_text = item
        for _attempt in range(2):
            batch_context = self._batch_context(original_text=original_text, item_text=attempt_text)
            try:
                command = await self._parser.parse(
                    text=attempt_text,
                    locale=user.language,
                    timezone=user.timezone,
                    user_id=user.id,
                    user_memory=user_memory,
                    context=batch_context,
                )
            except Exception as exc:
                mode, repaired, question = await self._parser.repair_operation(
                    text=original_text,
                    failed_operation=attempt_text,
                    reason=f"parse_error:{exc}",
                    locale=user.language,
                    timezone=user.timezone,
                    user_memory=user_memory,
                    context=batch_context,
                )
                if mode == "skip":
                    return None
                if mode == "retry" and repaired:
                    attempt_text = repaired
                    continue
                return AssistantResponse(
                    question
                    or await self._ask_clarification(
                        user=user,
                        source_text=original_text,
                        reason=f"Не удалось распарсить шаг операции: {attempt_text}",
                        fallback="Уточните, пожалуйста, шаг операции.",
                        user_memory=user_memory,
                        context=batch_context,
                    )
                )

            if isinstance(command, ClarifyCommand):
                mode, repaired, question = await self._parser.repair_operation(
                    text=original_text,
                    failed_operation=attempt_text,
                    reason=command.question,
                    locale=user.language,
                    timezone=user.timezone,
                    user_memory=user_memory,
                    context=batch_context,
                )
                if mode == "skip":
                    return None
                if mode == "retry" and repaired:
                    attempt_text = repaired
                    continue
                return AssistantResponse(
                    question
                    or await self._ask_clarification(
                        user=user,
                        source_text=original_text,
                        reason=command.question,
                        fallback=command.question,
                        user_memory=user_memory,
                        context=batch_context,
                    )
                )

            return await self._execute_command(user, command)

        return AssistantResponse(
            await self._ask_clarification(
                user=user,
                source_text=original_text,
                reason="После повтора шаг операции не удалось распарсить/выполнить.",
                fallback="Не удалось выполнить шаг операции. Уточните формулировку.",
                user_memory=user_memory,
                context=self._batch_context(original_text=original_text, item_text=item),
            )
        )

    def _batch_context(self, *, original_text: str, item_text: str) -> dict[str, object]:
        return {
            "batch_original_text": original_text,
            "batch_item": item_text,
        }

    def _is_failure_response(self, text: str) -> bool:
        low = text.lower()
        return low.startswith("не удалось") or low.startswith("ошибка")
