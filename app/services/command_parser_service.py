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
from app.services.smart_agents import (
    AmbiguityResolverAgent,
    BatchPlanCriticAgent,
    ClarificationQuestionAgent,
    CommandAgent,
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
    SmartGraphOrchestrator,
    TimeNormalizationAgent,
)
from app.services.smart_agents.llm_core import ClarifyAgent
from app.services.smart_agents.models import AgentGraphTrace, AgentStageTrace, UserMemoryProfile
from app.services.smart_agents.prompts import default_clarify_question

logger = structlog.get_logger(__name__)


class CommandParserService:
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
        self._batch_plan_critic = BatchPlanCriticAgent(llm_client)
        self._execution_supervisor = ExecutionSupervisorAgent(llm_client)
        self._plan_repair = PlanRepairAgent(llm_client)
        self._response_policy = ResponsePolicyAgent(llm_client)

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
    ) -> ParsedCommand:
        started = perf_counter()
        request_id = uuid4().hex
        with bound_contextvars(parser_request_id=request_id):
            logger.info("parser.parse_started", locale=locale, timezone=timezone, text_len=len(text))
            route_mode = self._select_route_mode(user_id=user_id, text=text)
            error_class: str | None = None
            try:
                result, trace = await self._graph.run_with_trace(
                    text=text,
                    locale=locale,
                    timezone=timezone,
                    route_mode=route_mode,
                    user_memory=(asdict(user_memory) if user_memory is not None else None),
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
    ) -> str | None:
        try:
            decision = await self._primary_assistant.decide(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=(asdict(user_memory) if user_memory is not None else None),
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
                user_memory=(asdict(user_memory) if user_memory is not None else None),
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
    ) -> tuple[str, list[str], str | None, str | None, str, bool]:
        memory = asdict(user_memory) if user_memory is not None else None
        try:
            route = await self._conversation_manager.route(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=memory,
            )
        except Exception:
            logger.exception("parser.conversation_manager_failed")
            return "commands", [text], None, None, "continue_on_error", False

        if route.mode == "answer":
            answer = (route.answer or "").strip()
            if answer and route.confidence >= 0.7:
                return "answer", [], answer, None, "continue_on_error", False
            helper_answer = await self.maybe_answer_help(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=user_memory,
            )
            if helper_answer:
                return "answer", [], helper_answer, None, "continue_on_error", False
            return "commands", [text], None, None, "continue_on_error", False

        if route.mode == "clarify":
            return "clarify", [], None, route.question or default_clarify_question(), "continue_on_error", False

        operations = route.operations or [text]
        mode, reviewed_ops, question, exec_mode = await self.review_batch_plan(
            text=text,
            operations=operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        if mode == "clarify":
            return "clarify", [], None, question or default_clarify_question(), exec_mode, False
        strategy, stop_on_error = await self.supervise_execution(
            text=text,
            operations=reviewed_ops,
            execution_mode=exec_mode,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        return "commands", reviewed_ops, None, None, strategy, stop_on_error

    async def repair_operation(
        self,
        text: str,
        failed_operation: str,
        reason: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
    ) -> tuple[str, str | None, str | None]:
        try:
            decision = await self._plan_repair.repair(
                text=text,
                failed_operation=failed_operation,
                reason=reason,
                locale=locale,
                timezone=timezone,
                user_memory=(asdict(user_memory) if user_memory is not None else None),
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
    ) -> str:
        try:
            follow_up = await self._clarifier.run(
                text=f"Запрос: {text}\nКонтекст: {reason}",
                locale=locale,
                timezone=timezone,
                fallback=fallback,
                user_memory=(asdict(user_memory) if user_memory is not None else None),
            )
            question = follow_up.question.strip()
            return question or fallback
        except Exception:
            logger.exception("parser.generate_clarification_failed")
            return fallback

    async def review_batch_plan(
        self,
        text: str,
        operations: list[str],
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
    ) -> tuple[str, list[str], str | None, str]:
        if not operations:
            return "commands", [], None, "continue_on_error"

        try:
            decision = await self._batch_plan_critic.critique(
                text=text,
                operations=operations,
                locale=locale,
                timezone=timezone,
                user_memory=(asdict(user_memory) if user_memory is not None else None),
            )
        except Exception:
            logger.exception("parser.batch_plan_critic_failed")
            return "commands", operations, None, "continue_on_error"

        if decision.mode == "clarify":
            return "clarify", [], decision.question or default_clarify_question(), decision.execution_mode
        return "commands", (decision.operations or operations), None, decision.execution_mode

    async def supervise_execution(
        self,
        text: str,
        operations: list[str],
        execution_mode: str,
        locale: str,
        timezone: str,
        user_memory: UserMemoryProfile | None = None,
    ) -> tuple[str, bool]:
        try:
            decision = await self._execution_supervisor.supervise(
                text=text,
                operations=operations,
                execution_mode=execution_mode,
                locale=locale,
                timezone=timezone,
                user_memory=(asdict(user_memory) if user_memory is not None else None),
            )
            return decision.strategy, decision.stop_on_error
        except Exception:
            logger.exception("parser.execution_supervisor_failed")
            return ("partial_commit", execution_mode == "stop_on_error")

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
    ) -> str:
        try:
            decision = await self._response_policy.render(
                kind=kind,
                source_text=source_text,
                reason=reason,
                locale=locale,
                timezone=timezone,
                user_memory=(asdict(user_memory) if user_memory is not None else None),
            )
            text = (decision.text or "").strip()
            if decision.confidence >= 0.65 and text:
                return text
            return fallback
        except Exception:
            logger.exception("parser.response_policy_failed")
            return fallback

    def parse_payload(self, payload: dict[str, object]) -> ParsedCommand:
        return self._adapter.validate_python(payload)

    def _select_route_mode(self, user_id: int | None, text: str) -> str:
        if user_id is None:
            return "precise"
        key = f"{user_id}:{text[:12]}".encode()
        bucket = zlib.crc32(key) % 100
        return "fast" if bucket < 20 else "precise"

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
