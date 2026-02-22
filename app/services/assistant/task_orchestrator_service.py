from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

from app.services.smart_agents import (
    ConversationManagerAgent,
    ExecutionSupervisorAgent,
    RiskPolicyAgent,
    TaskChunkingAgent,
    TaskGraphAgent,
)
from app.services.smart_agents.models import UserMemoryProfile
from app.services.smart_agents.prompts import default_clarify_question

logger = structlog.get_logger(__name__)

AgentMemoryProvider = Callable[
    [str, str, UserMemoryProfile | None, dict[str, object] | None],
    Awaitable[dict[str, object] | None],
]
HelpAnswerProvider = Callable[
    [str, str, str, UserMemoryProfile | None, dict[str, object] | None],
    Awaitable[str | None],
]


class TaskOrchestratorService:
    def __init__(
        self,
        *,
        conversation_manager: ConversationManagerAgent,
        task_chunker: TaskChunkingAgent,
        task_graph: TaskGraphAgent,
        execution_supervisor: ExecutionSupervisorAgent,
        risk_policy: RiskPolicyAgent,
        memory_provider: AgentMemoryProvider,
        help_answer_provider: HelpAnswerProvider,
    ) -> None:
        self._conversation_manager = conversation_manager
        self._task_chunker = task_chunker
        self._task_graph = task_graph
        self._execution_supervisor = execution_supervisor
        self._risk_policy = risk_policy
        self._memory_provider = memory_provider
        self._help_answer_provider = help_answer_provider

    async def route_conversation(
        self,
        *,
        text: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[str, list[str], str | None, str | None, str, bool]:
        memory = await self._memory_provider(locale, timezone, user_memory, context)
        try:
            route = await self._conversation_manager.route(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=memory,
            )
        except Exception:
            logger.exception("orchestrator.conversation_manager_failed")
            return "commands", [text], None, None, "continue_on_error", False

        if route.mode == "answer":
            answer = (route.answer or "").strip()
            if answer and route.confidence >= 0.7:
                return "answer", [], answer, None, "continue_on_error", False
            helper_answer = await self._help_answer_provider(text, locale, timezone, user_memory, context)
            if helper_answer:
                return "answer", [], helper_answer, None, "continue_on_error", False
            return "commands", [text], None, None, "continue_on_error", False

        if route.mode == "clarify":
            return "clarify", [], None, route.question or default_clarify_question(), "continue_on_error", False

        operations = route.operations or [text]
        operations = await self.extract_task_operations(
            text=text,
            fallback_operations=operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        operations, execution_mode = await self.plan_task_graph(
            text=text,
            operations=operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        strategy, stop_on_error = await self.supervise_execution(
            text=text,
            operations=operations,
            execution_mode=execution_mode,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        return "commands", operations, None, None, strategy, stop_on_error

    async def extract_task_operations(
        self,
        *,
        text: str,
        fallback_operations: list[str],
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> list[str]:
        if not text.strip():
            return fallback_operations
        if len(fallback_operations) > 1:
            return fallback_operations
        if len(text) < 80 and "\n" not in text:
            return fallback_operations
        memory = await self._memory_provider(locale, timezone, user_memory, context)
        try:
            decision = await self._task_chunker.chunk(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=memory,
            )
        except Exception:
            logger.exception("orchestrator.task_chunker_failed")
            return fallback_operations
        if decision.confidence < 0.6 or not decision.operations:
            return fallback_operations
        return decision.operations

    async def plan_task_graph(
        self,
        *,
        text: str,
        operations: list[str],
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[list[str], str]:
        memory = await self._memory_provider(locale, timezone, user_memory, context)
        try:
            decision = await self._task_graph.plan(
                text=text,
                operations=operations,
                locale=locale,
                timezone=timezone,
                user_memory=memory,
            )
        except Exception:
            logger.exception("orchestrator.task_graph_failed")
            return operations, "continue_on_error"
        if decision.confidence < 0.6:
            return operations, "continue_on_error"
        execution_mode = (
            decision.execution_mode
            if decision.execution_mode in {"continue_on_error", "stop_on_error"}
            else "continue_on_error"
        )
        return (decision.operations or operations), execution_mode

    async def assess_plan_risk(
        self,
        *,
        text: str,
        operations: list[str],
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[bool, str, str]:
        if not operations:
            return False, "low", ""
        memory = await self._memory_provider(locale, timezone, user_memory, context)
        try:
            decision = await self._risk_policy.assess(
                text=text,
                operations=operations,
                locale=locale,
                timezone=timezone,
                user_memory=memory,
            )
        except Exception:
            logger.exception("orchestrator.risk_policy_failed")
            return False, "low", ""
        if decision.confidence < 0.6:
            return False, "low", ""
        return decision.requires_confirmation, decision.risk_level, (decision.summary or "").strip()

    async def supervise_execution(
        self,
        *,
        text: str,
        operations: list[str],
        execution_mode: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[str, bool]:
        memory = await self._memory_provider(locale, timezone, user_memory, context)
        try:
            decision = await self._execution_supervisor.supervise(
                text=text,
                operations=operations,
                execution_mode=execution_mode,
                locale=locale,
                timezone=timezone,
                user_memory=memory,
            )
            return decision.strategy, decision.stop_on_error
        except Exception:
            logger.exception("orchestrator.execution_supervisor_failed")
            return ("partial_commit", execution_mode == "stop_on_error")
