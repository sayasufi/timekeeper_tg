from __future__ import annotations

import json
from time import perf_counter
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.domain.commands import ClarifyCommand, ParsedCommand
from app.domain.enums import Intent
from app.services.parser.json_recovery import recover_json_object
from app.services.smart_agents.advanced_agents import (
    AmbiguityResolverAgent,
    EntityExtractionAgent,
    FollowUpPlannerAgent,
    IntentJudgeAgent,
    NoteLinkingAgent,
    RecoveryAndGuardrailAgent,
    RecurrenceUnderstandingAgent,
    ReminderPolicyAgent,
    TimeNormalizationAgent,
)
from app.services.smart_agents.models import AgentGraphTrace, AgentStageTrace
from app.services.smart_agents.prompts import default_clarify_question


class SmartGraphOrchestrator:
    def __init__(
        self,
        adapter: TypeAdapter[ParsedCommand],
        intent_judge: IntentJudgeAgent,
        entity_extractor: EntityExtractionAgent,
        time_normalizer: TimeNormalizationAgent,
        ambiguity_resolver: AmbiguityResolverAgent,
        followup_planner: FollowUpPlannerAgent,
        guardrail_agent: RecoveryAndGuardrailAgent,
        reminder_policy_agent: ReminderPolicyAgent,
        note_linking_agent: NoteLinkingAgent,
        recurrence_agent: RecurrenceUnderstandingAgent,
    ) -> None:
        self._adapter = adapter
        self._intent_judge = intent_judge
        self._entity_extractor = entity_extractor
        self._time_normalizer = time_normalizer
        self._ambiguity_resolver = ambiguity_resolver
        self._followup_planner = followup_planner
        self._guardrail_agent = guardrail_agent
        self._reminder_policy_agent = reminder_policy_agent
        self._note_linking_agent = note_linking_agent
        self._recurrence_agent = recurrence_agent

    async def run(
        self,
        text: str,
        locale: str,
        timezone: str,
        route_mode: str = "precise",
        user_memory: dict[str, Any] | None = None,
    ) -> ParsedCommand:
        command, _trace = await self.run_with_trace(
            text=text,
            locale=locale,
            timezone=timezone,
            route_mode=route_mode,
            user_memory=user_memory,
        )
        return command

    async def run_with_trace(
        self,
        text: str,
        locale: str,
        timezone: str,
        route_mode: str = "precise",
        user_memory: dict[str, Any] | None = None,
    ) -> tuple[ParsedCommand, AgentGraphTrace]:
        schema = self._adapter.json_schema()
        graph_started = perf_counter()
        stages: list[AgentStageTrace] = []
        selected_path: list[str] = []
        recovery_used = False

        stage_started = perf_counter()
        judge = await self._intent_judge.run(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        stages.append(
            AgentStageTrace(
                stage="intent_judge",
                duration_ms=int((perf_counter() - stage_started) * 1000),
                confidence=judge.confidence,
                metadata={"intent": judge.intent, "prompt_version": "v1", "input_chars": str(len(text))},
            )
        )
        selected_path.append("judge")

        if judge.needs_clarification or judge.intent == Intent.CLARIFY.value:
            stage_started = perf_counter()
            question = await self._followup_planner.plan(
                text=text,
                locale=locale,
                timezone=timezone,
                fallback=judge.question or default_clarify_question(),
                user_memory=user_memory,
            )
            stages.append(
                AgentStageTrace(
                    stage="followup",
                    duration_ms=int((perf_counter() - stage_started) * 1000),
                    confidence=question.confidence,
                    metadata={"why": question.why, "prompt_version": "v1"},
                )
            )
            selected_path.append("clarify")
            return ClarifyCommand(intent=Intent.CLARIFY, question=question.question), AgentGraphTrace(
                route_mode=route_mode,
                stages=stages,
                selected_path=selected_path,
                overall_confidence=judge.confidence,
                total_duration_ms=int((perf_counter() - graph_started) * 1000),
            )

        stage_started = perf_counter()
        raw_command, extract_confidence = await self._entity_extractor.run(
            text=text,
            locale=locale,
            timezone=timezone,
            intent=judge.intent,
            schema=schema,
            user_memory=user_memory,
        )
        stages.append(
            AgentStageTrace(
                stage="entity_extraction",
                duration_ms=int((perf_counter() - stage_started) * 1000),
                confidence=extract_confidence,
                metadata={"prompt_version": "v1"},
            )
        )
        selected_path.append("extract")
        command: ParsedCommand

        try:
            loaded = recover_json_object(raw_command)
            command = self._adapter.validate_python(loaded)
            selected_path.append("validate")
        except (ValidationError, ValueError, json.JSONDecodeError, SyntaxError):
            stage_started = perf_counter()
            repaired = await self._guardrail_agent.recover(
                raw_command=raw_command,
                locale=locale,
                timezone=timezone,
                intent=judge.intent,
                schema=schema,
                user_memory=user_memory,
            )
            recovery_used = True
            stages.append(
                AgentStageTrace(
                    stage="recovery_guardrail",
                    duration_ms=int((perf_counter() - stage_started) * 1000),
                    metadata={"prompt_version": "v1"},
                )
            )
            selected_path.append("recover")

            if repaired is None:
                stage_started = perf_counter()
                question = await self._followup_planner.plan(
                    text=text,
                    locale=locale,
                    timezone=timezone,
                    fallback=default_clarify_question(),
                    user_memory=user_memory,
                )
                stages.append(
                    AgentStageTrace(
                        stage="followup",
                        duration_ms=int((perf_counter() - stage_started) * 1000),
                        confidence=question.confidence,
                        metadata={"why": question.why, "prompt_version": "v1"},
                    )
                )
                selected_path.append("clarify")
                return ClarifyCommand(intent=Intent.CLARIFY, question=question.question), AgentGraphTrace(
                    route_mode=route_mode,
                    stages=stages,
                    selected_path=selected_path,
                    overall_confidence=min(judge.confidence, extract_confidence),
                    total_duration_ms=int((perf_counter() - graph_started) * 1000),
                )
            command = repaired

        stage_started = perf_counter()
        recurrence_confidence = 0.0
        try:
            recurrence = await self._recurrence_agent.run(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=user_memory,
            )
            recurrence_confidence = recurrence.confidence
            command = self._recurrence_agent.apply(command, recurrence)
            selected_path.append("recurrence")
        except Exception:
            selected_path.append("recurrence_skip")
        stages.append(
            AgentStageTrace(
                stage="recurrence",
                duration_ms=int((perf_counter() - stage_started) * 1000),
                confidence=recurrence_confidence,
                metadata={"prompt_version": "v1"},
            )
        )

        stage_started = perf_counter()
        command = self._time_normalizer.run(command=command, timezone=timezone, locale=locale)
        command = self._reminder_policy_agent.apply_default_offsets(command)
        command = self._note_linking_agent.link(command)
        stages.append(
            AgentStageTrace(
                stage="normalize_policy_link",
                duration_ms=int((perf_counter() - stage_started) * 1000),
                metadata={"prompt_version": "v1"},
            )
        )
        selected_path.append("normalize")

        threshold = 0.65 if route_mode == "fast" else 0.75
        effective_confidence = min(judge.confidence, extract_confidence)
        if recovery_used:
            effective_confidence = max(effective_confidence, 0.8)
        if self._ambiguity_resolver.should_resolve(effective_confidence, threshold):
            stage_started = perf_counter()
            question = await self._followup_planner.plan(
                text=text,
                locale=locale,
                timezone=timezone,
                fallback="Есть несколько трактовок. Уточните запрос, пожалуйста.",
                user_memory=user_memory,
            )
            stages.append(
                AgentStageTrace(
                    stage="followup",
                    duration_ms=int((perf_counter() - stage_started) * 1000),
                    confidence=question.confidence,
                    metadata={"why": question.why, "prompt_version": "v1"},
                )
            )
            selected_path.append("clarify")
            return ClarifyCommand(intent=Intent.CLARIFY, question=question.question), AgentGraphTrace(
                route_mode=route_mode,
                stages=stages,
                selected_path=selected_path,
                overall_confidence=effective_confidence,
                total_duration_ms=int((perf_counter() - graph_started) * 1000),
            )

        selected_path.append("execute")
        if recovery_used:
            selected_path.append("guardrail_applied")

        return command, AgentGraphTrace(
            route_mode=route_mode,
            stages=stages,
            selected_path=selected_path,
            overall_confidence=effective_confidence,
            total_duration_ms=int((perf_counter() - graph_started) * 1000),
        )
