from __future__ import annotations

from time import perf_counter
from typing import Any

import structlog

from app.domain.enums import Intent
from app.integrations.llm.base import LLMClient
from app.services.json_recovery import recover_json_object
from app.services.smart_agents.models import (
    AgentOutput,
    BatchPlanCriticDecision,
    BotReplyDecision,
    ChoiceOptionsDecision,
    ContextCompressionDecision,
    ConversationRouteDecision,
    ExecutionPathDecision,
    ExecutionSupervisionDecision,
    HelpKnowledgeDecision,
    IntentDecision,
    PlanRepairDecision,
    PrimaryAssistantDecision,
    RecurrenceDecision,
    ResponsePolicyDecision,
    TelegramFormatDecision,
)
from app.services.smart_agents.prompts import (
    build_batch_plan_critic_prompt,
    build_bot_reply_prompt,
    build_choice_options_prompt,
    build_clarify_prompt,
    build_command_prompt,
    build_context_compressor_prompt,
    build_conversation_manager_prompt,
    build_execution_path_prompt,
    build_execution_supervisor_prompt,
    build_help_knowledge_prompt,
    build_intent_prompt,
    build_plan_repair_prompt,
    build_primary_assistant_prompt,
    build_recovery_prompt,
    build_recurrence_prompt,
    build_response_policy_prompt,
    build_telegram_format_prompt,
    default_clarify_question,
)

logger = structlog.get_logger(__name__)


class BaseLLMAgent:
    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    async def _complete(self, prompt: str, *, stage: str) -> str:
        started = perf_counter()
        logger.info("agent.llm_request_started", stage=stage, prompt_len=len(prompt))
        try:
            response = await self._llm_client.complete(prompt)
        except Exception:
            logger.exception("agent.llm_request_failed", stage=stage)
            raise
        logger.info(
            "agent.llm_request_completed",
            stage=stage,
            duration_ms=int((perf_counter() - started) * 1000),
            response_len=len(response),
        )
        return response

    def _parse_output(self, raw: str) -> AgentOutput:
        loaded = recover_json_object(raw)
        if "result" in loaded:
            result = loaded.get("result")
            if isinstance(result, dict):
                parsed_result = result
            else:
                parsed_result = {"value": result}
            return AgentOutput(
                result=parsed_result,
                confidence=float(loaded.get("confidence", 0.75)),
                needs_clarification=bool(loaded.get("needs_clarification", False)),
                clarify_question=(
                    str(loaded.get("clarify_question"))
                    if loaded.get("clarify_question") is not None
                    else None
                ),
                reasons=[str(item) for item in loaded.get("reasons", []) if isinstance(item, str)],
            )

        # backward compatibility with legacy direct-json outputs
        return AgentOutput(
            result=loaded,
            confidence=0.8,
            needs_clarification=bool(loaded.get("needs_clarification", False)),
            clarify_question=str(loaded.get("question")) if loaded.get("question") is not None else None,
            reasons=[],
        )


class IntentAgent(BaseLLMAgent):
    async def decide(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> IntentDecision:
        raw = await self._complete(
            build_intent_prompt(text=text, locale=locale, timezone=timezone, user_memory=user_memory),
            stage="intent",
        )
        parsed = self._parse_output(raw)

        decision = IntentDecision(
            intent=str(parsed.result.get("intent", Intent.CLARIFY.value)),
            needs_clarification=parsed.needs_clarification,
            question=parsed.clarify_question,
        )
        normalized = decision.normalized_intent()
        if normalized == Intent.CLARIFY.value:
            decision.intent = Intent.CLARIFY.value
            decision.needs_clarification = True

        if decision.intent == Intent.CLARIFY.value and not decision.question:
            decision.question = default_clarify_question()

        logger.info(
            "agent.intent_decision",
            intent=decision.intent,
            needs_clarification=decision.needs_clarification,
        )
        return decision


class CommandAgent(BaseLLMAgent):
    async def build_command(
        self,
        text: str,
        locale: str,
        timezone: str,
        intent: str,
        schema: dict[str, Any],
        user_memory: dict[str, Any] | None = None,
    ) -> AgentOutput:
        prompt = build_command_prompt(
            text=text,
            locale=locale,
            timezone=timezone,
            intent=intent,
            schema=schema,
            user_memory=user_memory,
        )
        raw = await self._complete(prompt, stage="command")
        try:
            return self._parse_output(raw)
        except Exception:
            return AgentOutput(
                result={"_raw": raw},
                confidence=0.1,
                needs_clarification=False,
                clarify_question=None,
                reasons=["command_json_invalid"],
            )


class RecoveryAgent(BaseLLMAgent):
    async def recover_command(
        self,
        raw_command: str,
        locale: str,
        timezone: str,
        intent: str,
        schema: dict[str, Any],
        user_memory: dict[str, Any] | None = None,
    ) -> AgentOutput:
        prompt = build_recovery_prompt(
            raw_command=raw_command,
            locale=locale,
            timezone=timezone,
            intent=intent,
            schema=schema,
            user_memory=user_memory,
        )
        raw = await self._complete(prompt, stage="recovery")
        return self._parse_output(raw)


class ClarifyAgent(BaseLLMAgent):
    async def ask(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> tuple[str, str, float]:
        prompt = build_clarify_prompt(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="clarify"))
        question = str(parsed.result.get("question", "")).strip() or default_clarify_question()
        why = str(parsed.result.get("why", "missing_required_data")).strip()
        return question, why, parsed.confidence


class RecurrenceAgent(BaseLLMAgent):
    async def parse(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> RecurrenceDecision:
        prompt = build_recurrence_prompt(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="recurrence"))
        return RecurrenceDecision(
            rrule=str(parsed.result.get("rrule")) if parsed.result.get("rrule") is not None else None,
            until=str(parsed.result.get("until")) if parsed.result.get("until") is not None else None,
            confidence=parsed.confidence,
            needs_clarification=parsed.needs_clarification,
            clarify_question=parsed.clarify_question,
        )


class PrimaryAssistantAgent(BaseLLMAgent):
    async def decide(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> PrimaryAssistantDecision:
        prompt = build_primary_assistant_prompt(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="primary_assistant"))
        mode_raw = str(parsed.result.get("mode", "delegate")).lower()
        mode = "answer" if mode_raw == "answer" else "delegate"
        answer = str(parsed.result.get("answer")) if parsed.result.get("answer") is not None else None
        return PrimaryAssistantDecision(mode=mode, answer=answer, confidence=parsed.confidence)


class HelpKnowledgeAgent(BaseLLMAgent):
    async def answer(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> HelpKnowledgeDecision:
        prompt = build_help_knowledge_prompt(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="help_knowledge"))
        answer = str(parsed.result.get("answer")) if parsed.result.get("answer") is not None else None
        return HelpKnowledgeDecision(answer=answer, confidence=parsed.confidence)


class BotReplyAgent(BaseLLMAgent):
    async def render(
        self,
        *,
        raw_text: str,
        user_text: str | None,
        locale: str,
        timezone: str,
        response_kind: str,
        user_memory: dict[str, Any] | None = None,
    ) -> BotReplyDecision:
        prompt = build_bot_reply_prompt(
            raw_text=raw_text,
            user_text=user_text,
            locale=locale,
            timezone=timezone,
            response_kind=response_kind,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="bot_reply"))
        text = str(parsed.result.get("text")) if parsed.result.get("text") is not None else None
        return BotReplyDecision(text=text, confidence=parsed.confidence)


class PlanRepairAgent(BaseLLMAgent):
    async def repair(
        self,
        text: str,
        failed_operation: str,
        reason: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> PlanRepairDecision:
        prompt = build_plan_repair_prompt(
            text=text,
            failed_operation=failed_operation,
            reason=reason,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="plan_repair"))
        mode_raw = str(parsed.result.get("mode", "clarify")).lower()
        mode = mode_raw if mode_raw in {"retry", "skip", "clarify"} else "clarify"
        operation = str(parsed.result.get("operation")) if parsed.result.get("operation") is not None else None
        question = str(parsed.result.get("question")) if parsed.result.get("question") is not None else None
        return PlanRepairDecision(
            mode=mode,
            operation=(operation.strip() if operation else None),
            question=question,
            confidence=parsed.confidence,
        )


class ConversationManagerAgent(BaseLLMAgent):
    async def route(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> ConversationRouteDecision:
        prompt = build_conversation_manager_prompt(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="conversation_manager"))
        mode_raw = str(parsed.result.get("mode", "commands")).lower()
        mode = mode_raw if mode_raw in {"commands", "answer", "clarify"} else "commands"
        operations_raw = parsed.result.get("operations")
        operations: list[str] = []
        if isinstance(operations_raw, list):
            operations = [str(item).strip() for item in operations_raw if str(item).strip()]
        answer = str(parsed.result.get("answer")) if parsed.result.get("answer") is not None else None
        question = str(parsed.result.get("question")) if parsed.result.get("question") is not None else None
        return ConversationRouteDecision(
            mode=mode,
            operations=operations,
            answer=answer,
            question=question,
            confidence=parsed.confidence,
        )


class BatchPlanCriticAgent(BaseLLMAgent):
    async def critique(
        self,
        text: str,
        operations: list[str],
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> BatchPlanCriticDecision:
        prompt = build_batch_plan_critic_prompt(
            text=text,
            operations=operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="batch_plan_critic"))
        mode_raw = str(parsed.result.get("mode", "commands")).lower()
        mode = "clarify" if mode_raw == "clarify" else "commands"
        operations_raw = parsed.result.get("operations")
        reviewed_ops: list[str] = []
        if isinstance(operations_raw, list):
            reviewed_ops = [str(item).strip() for item in operations_raw if str(item).strip()]
        question = str(parsed.result.get("question")) if parsed.result.get("question") is not None else None
        execution_mode_raw = str(parsed.result.get("execution_mode", "continue_on_error")).lower()
        execution_mode = execution_mode_raw if execution_mode_raw in {"continue_on_error", "stop_on_error"} else "continue_on_error"
        return BatchPlanCriticDecision(
            mode=mode,
            operations=reviewed_ops,
            question=question,
            execution_mode=execution_mode,
            confidence=parsed.confidence,
        )


class ExecutionPathAgent(BaseLLMAgent):
    async def decide(
        self,
        *,
        text: str,
        operations: list[str],
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> ExecutionPathDecision:
        prompt = build_execution_path_prompt(
            text=text,
            operations=operations,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="execution_path"))
        path_raw = str(parsed.result.get("path", "full")).lower()
        path = "fast" if path_raw == "fast" else "full"
        return ExecutionPathDecision(path=path, confidence=parsed.confidence)


class ExecutionSupervisorAgent(BaseLLMAgent):
    async def supervise(
        self,
        text: str,
        operations: list[str],
        execution_mode: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> ExecutionSupervisionDecision:
        prompt = build_execution_supervisor_prompt(
            text=text,
            operations=operations,
            execution_mode=execution_mode,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="execution_supervisor"))
        strategy_raw = str(parsed.result.get("strategy", "partial_commit")).lower()
        strategy = strategy_raw if strategy_raw in {"all_or_nothing", "partial_commit"} else "partial_commit"
        stop_on_error = bool(parsed.result.get("stop_on_error", execution_mode == "stop_on_error"))
        return ExecutionSupervisionDecision(
            strategy=strategy,
            stop_on_error=stop_on_error,
            confidence=parsed.confidence,
        )


class ResponsePolicyAgent(BaseLLMAgent):
    async def render(
        self,
        *,
        kind: str,
        source_text: str,
        reason: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> ResponsePolicyDecision:
        prompt = build_response_policy_prompt(
            kind=kind,
            source_text=source_text,
            reason=reason,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="response_policy"))
        text = str(parsed.result.get("text")) if parsed.result.get("text") is not None else None
        return ResponsePolicyDecision(text=text, confidence=parsed.confidence)


class ContextCompressorAgent(BaseLLMAgent):
    async def compress(
        self,
        *,
        context: dict[str, Any],
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> ContextCompressionDecision:
        prompt = build_context_compressor_prompt(
            context=context,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="context_compressor"))
        summary = str(parsed.result.get("summary", "")).strip()
        facts_raw = parsed.result.get("facts")
        facts: list[str] = []
        if isinstance(facts_raw, list):
            facts = [str(item).strip() for item in facts_raw if str(item).strip()]
        return ContextCompressionDecision(
            summary=summary,
            facts=facts,
            confidence=parsed.confidence,
        )


class TelegramFormattingAgent(BaseLLMAgent):
    async def format(
        self,
        *,
        text: str,
        response_kind: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> TelegramFormatDecision:
        prompt = build_telegram_format_prompt(
            text=text,
            response_kind=response_kind,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="telegram_format"))
        formatted = str(parsed.result.get("text")) if parsed.result.get("text") is not None else None
        return TelegramFormatDecision(text=formatted, confidence=parsed.confidence)


class ChoiceOptionsAgent(BaseLLMAgent):
    async def suggest(
        self,
        *,
        reply_text: str,
        response_kind: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> ChoiceOptionsDecision:
        prompt = build_choice_options_prompt(
            reply_text=reply_text,
            response_kind=response_kind,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        parsed = self._parse_output(await self._complete(prompt, stage="choice_options"))
        options_raw = parsed.result.get("options")
        options: list[str] = []
        if isinstance(options_raw, list):
            options = [str(item).strip() for item in options_raw if str(item).strip()]
        return ChoiceOptionsDecision(options=options, confidence=parsed.confidence)
