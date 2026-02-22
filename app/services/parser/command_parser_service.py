from __future__ import annotations

import json
import zlib
from dataclasses import asdict
from time import perf_counter
from uuid import uuid4

import structlog
from pydantic import TypeAdapter
from structlog.contextvars import bound_contextvars

from app.db.models import AgentRunTrace
from app.domain.commands import ClarifyCommand, ParsedCommand
from app.domain.enums import Intent
from app.integrations.llm.base import LLMClient
from app.repositories.agent_run_trace_repository import AgentRunTraceRepository
from app.services.assistant.task_orchestrator_service import TaskOrchestratorService
from app.services.smart_agents import (
    AmbiguityResolverAgent,
    ChoiceOptionsAgent,
    ClarificationQuestionAgent,
    CommandAgent,
    ContextCompressorAgent,
    ConversationManagerAgent,
    EntityExtractionAgent,
    ExecutionSupervisorAgent,
    FollowUpPlannerAgent,
    HelpKnowledgeAgent,
    IntentAgent,
    IntentJudgeAgent,
    NoteLinkingAgent,
    PlanRepairAgent,
    PrimaryAssistantAgent,
    RecoveryAgent,
    RecoveryAndGuardrailAgent,
    RecurrenceAgent,
    RecurrenceUnderstandingAgent,
    ReminderPolicyAgent,
    ResponsePolicyAgent,
    RiskPolicyAgent,
    SmartGraphOrchestrator,
    TaskChunkingAgent,
    TaskGraphAgent,
    TimeNormalizationAgent,
)
from app.services.smart_agents.llm_core import ClarifyAgent
from app.services.smart_agents.models import AgentGraphTrace, AgentStageTrace, UserMemoryProfile
from app.services.smart_agents.prompts import default_clarify_question

logger = structlog.get_logger(__name__)


class CommandParserService:
    _MAX_DIALOG_HISTORY_ITEMS = 8
    _TEMPORAL_CONTEXT_KEYS = (
        "now_utc_iso",
        "now_local_iso",
        "today_local_date",
        "today_local_weekday_iso",
        "today_local_weekday",
    )

    def __init__(
        self,
        llm_client: LLMClient,
        trace_repository: AgentRunTraceRepository | None = None,
    ) -> None:
        self._adapter: TypeAdapter[ParsedCommand] = TypeAdapter(ParsedCommand)
        self._trace_repository = trace_repository

        base_intent = IntentAgent(llm_client)
        base_command = CommandAgent(llm_client)
        base_recovery = RecoveryAgent(llm_client)
        base_clarify = ClarifyAgent(llm_client)
        base_recurrence = RecurrenceAgent(llm_client)
        self._clarifier = ClarificationQuestionAgent(base_clarify)
        self._primary_assistant = PrimaryAssistantAgent(llm_client)
        self._help_knowledge = HelpKnowledgeAgent(llm_client)
        self._conversation_manager = ConversationManagerAgent(llm_client)
        self._context_compressor = ContextCompressorAgent(llm_client)
        self._execution_supervisor = ExecutionSupervisorAgent(llm_client)
        self._task_chunker = TaskChunkingAgent(llm_client)
        self._task_graph = TaskGraphAgent(llm_client)
        self._risk_policy = RiskPolicyAgent(llm_client)
        self._plan_repair = PlanRepairAgent(llm_client)
        self._response_policy = ResponsePolicyAgent(llm_client)
        self._choice_options = ChoiceOptionsAgent(llm_client)
        self._task_orchestrator = TaskOrchestratorService(
            conversation_manager=self._conversation_manager,
            task_chunker=self._task_chunker,
            task_graph=self._task_graph,
            execution_supervisor=self._execution_supervisor,
            risk_policy=self._risk_policy,
            memory_provider=self._agent_memory_for_orchestrator,
            help_answer_provider=self._help_answer_for_orchestrator,
        )

        self._graph = SmartGraphOrchestrator(
            adapter=self._adapter,
            intent_judge=IntentJudgeAgent(base_intent),
            entity_extractor=EntityExtractionAgent(base_command),
            time_normalizer=TimeNormalizationAgent(),
            ambiguity_resolver=AmbiguityResolverAgent(),
            followup_planner=FollowUpPlannerAgent(ClarificationQuestionAgent(base_clarify)),
            guardrail_agent=RecoveryAndGuardrailAgent(base_recovery, self._adapter),
            reminder_policy_agent=ReminderPolicyAgent(),
            note_linking_agent=NoteLinkingAgent(),
            recurrence_agent=RecurrenceUnderstandingAgent(base_recurrence),
        )

    async def parse(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_id: int | None = None,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> ParsedCommand:
        started = perf_counter()
        request_id = uuid4().hex
        with bound_contextvars(parser_request_id=request_id):
            logger.info("parser.parse_started", locale=locale, timezone=timezone, text_len=len(text))
            route_mode = self._select_route_mode(user_id=user_id, text=text)
            error_class: str | None = None
            agent_memory = await self._agent_memory(
                locale=locale,
                timezone=timezone,
                user_memory=user_memory,
                context=context,
            )
            try:
                result, trace = await self._graph.run_with_trace(
                    text=text,
                    locale=locale,
                    timezone=timezone,
                    route_mode=route_mode,
                    user_memory=agent_memory,
                )
            except (ValueError, json.JSONDecodeError, SyntaxError) as exc:
                logger.warning("parser.graph_failed")
                error_class = exc.__class__.__name__
                result = ClarifyCommand(intent=Intent.CLARIFY, question=default_clarify_question())
                trace = AgentGraphTrace(
                    route_mode=route_mode,
                    stages=[],
                    selected_path=["graph_failed"],
                    overall_confidence=0.0,
                    total_duration_ms=int((perf_counter() - started) * 1000),
                )

            if trace is not None:
                if error_class is not None:
                    trace.stages.append(
                        self._build_error_stage_trace(error_class=error_class),
                    )
                await self._persist_trace(
                    user_id=user_id,
                    text=text,
                    locale=locale,
                    timezone=timezone,
                    result_intent=result.intent.value,
                    trace=trace,
                )

            logger.info(
                "parser.parse_completed",
                result_intent=result.intent.value,
                route_mode=route_mode,
                duration_ms=int((perf_counter() - started) * 1000),
            )
            return result

    def _build_error_stage_trace(self, error_class: str) -> AgentStageTrace:
        return AgentStageTrace(
            stage="error",
            duration_ms=0,
            confidence=0.0,
            metadata={"error_class": error_class, "prompt_version": "v1"},
        )

    async def maybe_answer_help(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> str | None:
        agent_memory = await self._agent_memory(
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        try:
            decision = await self._primary_assistant.decide(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=agent_memory,
            )
        except Exception:
            logger.exception("parser.primary_assistant_failed")
            return None

        if decision.mode != "answer":
            return None
        if decision.confidence < 0.75:
            return None
        fallback_answer = (decision.answer or "").strip()

        try:
            help_answer = await self._help_knowledge.answer(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=agent_memory,
            )
            resolved = (help_answer.answer or "").strip()
            if help_answer.confidence >= 0.65 and resolved:
                return resolved
        except Exception:
            logger.exception("parser.help_knowledge_failed")

        return fallback_answer or None

    async def route_conversation(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[str, list[str], str | None, str | None, str, bool]:
        return await self._task_orchestrator.route_conversation(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

    async def _extract_task_operations(
        self,
        *,
        text: str,
        fallback_operations: list[str],
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> list[str]:
        return await self._task_orchestrator.extract_task_operations(
            text=text,
            fallback_operations=fallback_operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

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
        return await self._task_orchestrator.plan_task_graph(
            text=text,
            operations=operations,
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
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[bool, str, str]:
        return await self._task_orchestrator.assess_plan_risk(
            text=text,
            operations=operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

    async def supervise_execution(
        self,
        text: str,
        operations: list[str],
        execution_mode: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[str, bool]:
        return await self._task_orchestrator.supervise_execution(
            text=text,
            operations=operations,
            execution_mode=execution_mode,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

    async def repair_operation(
        self,
        text: str,
        failed_operation: str,
        reason: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> tuple[str, str | None, str | None]:
        agent_memory = await self._agent_memory(
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        try:
            decision = await self._plan_repair.repair(
                text=text,
                failed_operation=failed_operation,
                reason=reason,
                locale=locale,
                timezone=timezone,
                user_memory=agent_memory,
            )
        except Exception:
            logger.exception("parser.plan_repair_failed")
            return "clarify", None, default_clarify_question()

        if decision.mode == "retry":
            return "retry", decision.operation, None
        if decision.mode == "skip":
            return "skip", None, None
        return "clarify", None, decision.question or default_clarify_question()

    async def generate_clarification(
        self,
        *,
        text: str,
        reason: str,
        locale: str,
        timezone: str,
        fallback: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> str:
        agent_memory = await self._agent_memory(
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        try:
            follow_up = await self._clarifier.run(
                text=f"Запрос: {text}\nКонтекст: {reason}",
                locale=locale,
                timezone=timezone,
                fallback=fallback,
                user_memory=agent_memory,
            )
            question = follow_up.question.strip()
            return question or fallback
        except Exception:
            logger.exception("parser.generate_clarification_failed")
            return fallback

    async def _agent_memory_for_orchestrator(
        self,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None,
        context: dict[str, object] | None,
    ) -> dict[str, object] | None:
        return await self._agent_memory(
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

    async def _help_answer_for_orchestrator(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None,
        context: dict[str, object] | None,
    ) -> str | None:
        return await self.maybe_answer_help(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )

    async def render_policy_text(
        self,
        *,
        kind: str,
        source_text: str,
        reason: str,
        locale: str,
        timezone: str,
        fallback: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> str:
        agent_memory = await self._agent_memory(
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        try:
            decision = await self._response_policy.render(
                kind=kind,
                source_text=source_text,
                reason=reason,
                locale=locale,
                timezone=timezone,
                user_memory=agent_memory,
            )
            text = (decision.text or "").strip()
            if decision.confidence >= 0.65 and text:
                return text
            return fallback
        except Exception:
            logger.exception("parser.response_policy_failed")
            return fallback

    async def suggest_quick_replies(
        self,
        *,
        reply_text: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
        context: dict[str, object] | None = None,
    ) -> list[str]:
        if not reply_text.strip():
            return []
        agent_memory = await self._agent_memory(
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
            context=context,
        )
        response_kind = "regular_reply"
        if isinstance(context, dict):
            kind_value = context.get("response_kind")
            if kind_value is not None:
                response_kind = str(kind_value)
        try:
            decision = await self._choice_options.suggest(
                reply_text=reply_text,
                response_kind=response_kind,
                locale=locale,
                timezone=timezone,
                user_memory=agent_memory,
            )
        except Exception:
            logger.exception("parser.choice_options_failed")
            return []
        if decision.confidence < 0.7:
            return []
        options = [item for item in decision.options if item]
        if len(options) < 2 or len(options) > 3:
            return []
        return options
    def parse_payload(self, payload: dict[str, object]) -> ParsedCommand:
        return self._adapter.validate_python(payload)

    def _select_route_mode(self, user_id: int | None, text: str) -> str:
        if user_id is None:
            return "precise"
        key = f"{user_id}:{text[:12]}".encode()
        bucket = zlib.crc32(key) % 100
        return "fast" if bucket < 20 else "precise"

    @property
    def task_orchestrator(self) -> TaskOrchestratorService:
        return self._task_orchestrator

    def _memory_with_context(
        self,
        user_memory: UserMemoryProfile | None,
        context: dict[str, object] | None,
    ) -> dict[str, object] | None:
        memory: dict[str, object] | None = asdict(user_memory) if user_memory is not None else None
        if context is None:
            return memory
        if memory is None:
            return {"context": context}
        merged = dict(memory)
        merged["context"] = context
        return merged

    async def _agent_memory(
        self,
        *,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None,
        context: dict[str, object] | None,
    ) -> dict[str, object] | None:
        normalized_context = self._normalize_context(context)
        merged = self._memory_with_context(user_memory, normalized_context)
        if merged is None or context is None:
            return merged
        latest_user_text = normalized_context.get("latest_user_text")
        temporal_context = {
            key: normalized_context.get(key)
            for key in self._TEMPORAL_CONTEXT_KEYS
            if normalized_context.get(key) is not None
        }
        try:
            serialized = json.dumps(normalized_context, ensure_ascii=False)
        except Exception:
            return merged
        if len(serialized) <= 1200:
            if latest_user_text is not None:
                merged["latest_user_text"] = latest_user_text
            merged.update(temporal_context)
            return merged
        context_for_compression = {
            key: value
            for key, value in normalized_context.items()
            if key != "latest_user_text"
        }
        try:
            compressed = await self._context_compressor.compress(
                context=context_for_compression,
                locale=locale,
                timezone=timezone,
                user_memory=(asdict(user_memory) if user_memory is not None else None),
            )
        except Exception:
            logger.exception("parser.context_compressor_failed")
            return merged
        if compressed.confidence < 0.6:
            return merged
        compact_context = {
            "summary": compressed.summary,
            "facts": compressed.facts,
            "original_keys": sorted(context_for_compression.keys()),
        }
        result = dict(merged)
        result["context_compact"] = compact_context
        result.pop("context", None)
        if latest_user_text is not None:
            result["latest_user_text"] = latest_user_text
        result.update(temporal_context)
        return result

    def _normalize_context(self, context: dict[str, object] | None) -> dict[str, object] | None:
        if context is None:
            return None
        normalized = dict(context)
        history = normalized.get("dialog_history")
        if isinstance(history, list):
            trimmed: list[dict[str, str]] = []
            for item in history[-self._MAX_DIALOG_HISTORY_ITEMS :]:
                if isinstance(item, dict):
                    role = str(item.get("role", "user"))
                    content = str(item.get("content", "")).strip()
                    if content:
                        trimmed.append({"role": role, "content": content})
            normalized["dialog_history"] = trimmed
        return normalized

    async def _persist_trace(
        self,
        user_id: int | None,
        text: str,
        locale: str,
        timezone: str,
        result_intent: str,
        trace: AgentGraphTrace,
    ) -> None:
        if self._trace_repository is None:
            return

        stage_payload = [
            {
                "stage": item.stage,
                "duration_ms": item.duration_ms,
                "confidence": item.confidence,
                "metadata": item.metadata or {},
            }
            for item in trace.stages
        ]
        stage_payload.append(
            {
                "stage": "usage_estimate",
                "duration_ms": 0,
                "confidence": None,
                "metadata": {
                    "prompt_version": "v1",
                    "prompt_tokens_est": str(max(1, len(text) // 4)),
                    "completion_tokens_est": str(max(1, len(result_intent) // 2)),
                },
            }
        )

        db_trace = AgentRunTrace(
            user_id=user_id,
            source="assistant_text",
            input_text=text,
            locale=locale,
            timezone=timezone,
            route_mode=trace.route_mode,
            result_intent=result_intent,
            confidence=trace.overall_confidence,
            selected_path=trace.selected_path,
            stages=stage_payload,
            total_duration_ms=trace.total_duration_ms,
        )
        await self._trace_repository.create(db_trace)



