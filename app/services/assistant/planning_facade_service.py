from __future__ import annotations

from app.services.assistant.task_orchestrator_service import TaskOrchestratorService
from app.services.parser.command_parser_service import CommandParserService


class PlanningFacadeService:
    def __init__(
        self,
        *,
        parser: CommandParserService,
        task_orchestrator: TaskOrchestratorService | None,
    ) -> None:
        self._parser = parser
        self._task_orchestrator = task_orchestrator

    async def route_conversation(
        self,
        *,
        text: str,
        locale: str,
        timezone: str,
        user_memory: object,
        context: dict[str, object] | None,
    ) -> tuple[str, list[str], str | None, str | None, str, bool]:
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user_memory, UserMemoryProfile):
            return await self._parser.route_conversation(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=None,
                context=context,
            )
        if self._task_orchestrator is not None:
            return await self._task_orchestrator.route_conversation(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=user_memory,
                context=context,
            )
        return await self._parser.route_conversation(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

    async def assess_plan_risk(
        self,
        *,
        text: str,
        operations: list[str],
        locale: str,
        timezone: str,
        user_memory: object,
        context: dict[str, object] | None,
    ) -> tuple[bool, str, str]:
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user_memory, UserMemoryProfile):
            return await self._parser.assess_plan_risk(
                text=text,
                operations=operations,
                locale=locale,
                timezone=timezone,
                user_memory=None,
                context=context,
            )
        if self._task_orchestrator is not None:
            return await self._task_orchestrator.assess_plan_risk(
                text=text,
                operations=operations,
                locale=locale,
                timezone=timezone,
                user_memory=user_memory,
                context=context,
            )
        return await self._parser.assess_plan_risk(
            text=text,
            operations=operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

