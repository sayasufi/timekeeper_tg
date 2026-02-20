from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.enums import Intent


@dataclass(slots=True)
class IntentDecision:
    intent: str
    needs_clarification: bool
    question: str | None = None

    def normalized_intent(self) -> str:
        allowed = {intent.value for intent in Intent}
        if self.intent in allowed:
            return self.intent
        return Intent.CLARIFY.value


@dataclass(slots=True)
class JudgeResult:
    intent: str
    confidence: float
    needs_clarification: bool
    question: str | None = None


@dataclass(slots=True)
class ScheduleConflict:
    has_conflict: bool
    conflicting_ranges: list[tuple[datetime, datetime]]


@dataclass(slots=True)
class AgentStageTrace:
    stage: str
    duration_ms: int
    confidence: float | None = None
    metadata: dict[str, str] | None = None


@dataclass(slots=True)
class AgentGraphTrace:
    route_mode: str
    stages: list[AgentStageTrace]
    selected_path: list[str]
    overall_confidence: float
    total_duration_ms: int


@dataclass(slots=True)
class AgentOutput:
    result: dict[str, Any]
    confidence: float
    needs_clarification: bool
    clarify_question: str | None
    reasons: list[str]


@dataclass(slots=True)
class RecurrenceDecision:
    rrule: str | None
    until: str | None
    confidence: float
    needs_clarification: bool
    clarify_question: str | None


@dataclass(slots=True)
class DisambiguationCandidate:
    event_id: str
    label: str
    subtitle: str
    score: float


@dataclass(slots=True)
class ChangeImpact:
    requires_confirmation: bool
    summary: str
    risk_level: str
    reasons: list[str]


@dataclass(slots=True)
class FollowUpQuestion:
    question: str
    why: str
    confidence: float


@dataclass(slots=True)
class UserMemoryProfile:
    timezone: str
    locale: str
    default_offsets: list[int]
    work_days: list[int]
    time_format_24h: bool


@dataclass(slots=True)
class PrimaryAssistantDecision:
    mode: str
    answer: str | None
    confidence: float


@dataclass(slots=True)
class HelpKnowledgeDecision:
    answer: str | None
    confidence: float


@dataclass(slots=True)
class BotReplyDecision:
    text: str | None
    confidence: float


@dataclass(slots=True)
class PlanRepairDecision:
    mode: str
    operation: str | None
    question: str | None
    confidence: float


@dataclass(slots=True)
class ConversationRouteDecision:
    mode: str
    operations: list[str]
    answer: str | None
    question: str | None
    confidence: float


@dataclass(slots=True)
class BatchPlanCriticDecision:
    mode: str
    operations: list[str]
    question: str | None
    execution_mode: str
    confidence: float


@dataclass(slots=True)
class ExecutionSupervisionDecision:
    strategy: str
    stop_on_error: bool
    confidence: float


@dataclass(slots=True)
class ResponsePolicyDecision:
    text: str | None
    confidence: float


@dataclass(slots=True)
class ContextCompressionDecision:
    summary: str
    facts: list[str]
    confidence: float


@dataclass(slots=True)
class TelegramFormatDecision:
    text: str | None
    confidence: float


@dataclass(slots=True)
class ChoiceOptionsDecision:
    options: list[str]
    confidence: float
