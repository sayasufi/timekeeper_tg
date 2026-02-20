from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from app.core.datetime_utils import parse_datetime_input
from app.db.models import Event, User
from app.domain.commands import (
    CreateReminderCommand,
    DeleteReminderCommand,
    ParsedCommand,
    UpdateReminderCommand,
    UpdateScheduleCommand,
)
from app.services.smart_agents.llm_core import (
    ClarifyAgent,
    CommandAgent,
    IntentAgent,
    RecoveryAgent,
    RecurrenceAgent,
)
from app.services.smart_agents.models import (
    ChangeImpact,
    DisambiguationCandidate,
    FollowUpQuestion,
    JudgeResult,
    RecurrenceDecision,
    ScheduleConflict,
    UserMemoryProfile,
)


class IntentJudgeAgent:
    def __init__(self, intent_agent: IntentAgent) -> None:
        self._intent_agent = intent_agent

    async def run(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> JudgeResult:
        decision = await self._intent_agent.decide(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )
        confidence = 0.95 if not decision.needs_clarification else 0.5
        return JudgeResult(
            intent=decision.intent,
            confidence=confidence,
            needs_clarification=decision.needs_clarification,
            question=decision.question,
        )


class EntityExtractionAgent:
    def __init__(self, command_agent: CommandAgent) -> None:
        self._command_agent = command_agent

    async def run(
        self,
        text: str,
        locale: str,
        timezone: str,
        intent: str,
        schema: dict[str, Any],
        user_memory: dict[str, Any] | None = None,
    ) -> tuple[str, float]:
        parsed = await self._command_agent.build_command(
            text=text,
            locale=locale,
            timezone=timezone,
            intent=intent,
            schema=schema,
            user_memory=user_memory,
        )
        raw_payload = parsed.result.get("_raw")
        if isinstance(raw_payload, str):
            return raw_payload, parsed.confidence
        return json_dumps(parsed.result), parsed.confidence


class RecurrenceUnderstandingAgent:
    def __init__(self, recurrence_agent: RecurrenceAgent) -> None:
        self._recurrence_agent = recurrence_agent

    async def run(
        self,
        text: str,
        locale: str,
        timezone: str,
        user_memory: dict[str, Any] | None = None,
    ) -> RecurrenceDecision:
        return await self._recurrence_agent.parse(
            text=text,
            locale=locale,
            timezone=timezone,
            user_memory=user_memory,
        )

    def apply(self, command: ParsedCommand, decision: RecurrenceDecision) -> ParsedCommand:
        if decision.rrule is None:
            return command

        if isinstance(command, CreateReminderCommand):
            if not command.rrule:
                command.rrule = decision.rrule
            if decision.until:
                command.description = (
                    f"{command.description}\nДо: {decision.until}" if command.description else f"До: {decision.until}"
                )
        return command


class TimeNormalizationAgent:
    def run(self, command: ParsedCommand, timezone: str, locale: str) -> ParsedCommand:
        if isinstance(command, CreateReminderCommand) and command.start_at:
            parsed = parse_datetime_input(command.start_at, timezone, languages=[locale, "ru", "en"])
            if parsed is not None:
                command.start_at = parsed.astimezone(UTC).isoformat()

        if isinstance(command, UpdateReminderCommand) and command.start_at:
            parsed = parse_datetime_input(command.start_at, timezone, languages=[locale, "ru", "en"])
            if parsed is not None:
                command.start_at = parsed.astimezone(UTC).isoformat()
        return command


class RecoveryAndGuardrailAgent:
    def __init__(self, recovery_agent: RecoveryAgent, adapter: TypeAdapter[ParsedCommand]) -> None:
        self._recovery_agent = recovery_agent
        self._adapter = adapter

    async def recover(
        self,
        raw_command: str,
        locale: str,
        timezone: str,
        intent: str,
        schema: dict[str, Any],
        user_memory: dict[str, Any] | None = None,
    ) -> ParsedCommand | None:
        try:
            repaired = await self._recovery_agent.recover_command(
                raw_command=raw_command,
                locale=locale,
                timezone=timezone,
                intent=intent,
                schema=schema,
                user_memory=user_memory,
            )
            command = self._adapter.validate_python(repaired.result)
            return self._apply_guardrails(command)
        except (ValidationError, ValueError):
            return None

    def _apply_guardrails(self, command: ParsedCommand) -> ParsedCommand:
        if isinstance(command, CreateReminderCommand) and not command.title.strip():
            command.title = "Напоминание"
        return command


class ClarificationQuestionAgent:
    def __init__(self, clarify_agent: ClarifyAgent) -> None:
        self._clarify_agent = clarify_agent

    async def run(
        self,
        text: str,
        locale: str,
        timezone: str,
        fallback: str,
        user_memory: dict[str, Any] | None = None,
    ) -> FollowUpQuestion:
        try:
            question, why, confidence = await self._clarify_agent.ask(
                text=text,
                locale=locale,
                timezone=timezone,
                user_memory=user_memory,
            )
            return FollowUpQuestion(question=question, why=why, confidence=confidence)
        except Exception:
            return FollowUpQuestion(question=fallback, why="llm_error", confidence=0.0)


class FollowUpPlannerAgent:
    def __init__(self, clarification_agent: ClarificationQuestionAgent) -> None:
        self._clarification_agent = clarification_agent

    async def plan(
        self,
        text: str,
        locale: str,
        timezone: str,
        fallback: str,
        user_memory: dict[str, Any] | None = None,
    ) -> FollowUpQuestion:
        return await self._clarification_agent.run(
            text=text,
            locale=locale,
            timezone=timezone,
            fallback=fallback,
            user_memory=user_memory,
        )


class AmbiguityResolverAgent:
    def should_resolve(self, confidence: float, threshold: float = 0.75) -> bool:
        return confidence < threshold


class EventDisambiguationAgent:
    def rank(
        self,
        *,
        search_text: str,
        candidates: list[Event],
        timezone: str,
    ) -> list[DisambiguationCandidate]:
        tz = ZoneInfo(timezone)
        needle = search_text.lower().strip()
        ranked: list[DisambiguationCandidate] = []

        for item in candidates:
            title = item.title.strip()
            score = 0.5
            if title.lower() == needle:
                score = 0.99
            elif needle and needle in title.lower():
                score = 0.8
            local_dt = item.starts_at.astimezone(tz)
            subtitle = f"{local_dt.strftime('%d.%m %H:%M')} • {item.event_type}"
            ranked.append(
                DisambiguationCandidate(
                    event_id=str(item.id),
                    label=title,
                    subtitle=subtitle,
                    score=score,
                )
            )

        ranked.sort(key=lambda x: x.score, reverse=True)
        return ranked


class ChangeImpactAgent:
    def build(self, command: ParsedCommand, target: Event) -> ChangeImpact:
        reasons: list[str] = []
        risk = "low"

        if isinstance(command, DeleteReminderCommand):
            reasons.append("операция удаления необратима")
            if target.event_type in {"lesson", "birthday"}:
                reasons.append("удаляется важный тип события")
                risk = "high"
            else:
                risk = "medium"
            summary = f"Будет удалено событие '{target.title}' ({target.event_type})."
            return ChangeImpact(True, summary, risk, reasons)

        if isinstance(command, UpdateScheduleCommand) and command.delete:
            reasons.append("удаление слота расписания")
            return ChangeImpact(True, f"Будет удален урок '{target.title}'.", "high", reasons)

        if isinstance(command, UpdateReminderCommand):
            changed: list[str] = []
            if command.title and command.title != target.title:
                changed.append(f"название: '{target.title}' -> '{command.title}'")
            if command.start_at:
                changed.append("время события")
            if command.rrule is not None:
                changed.append("правило повторения")
            if command.remind_offsets is not None:
                changed.append("настройки напоминаний")

            if changed:
                summary = "Изменятся поля: " + ", ".join(changed) + "."
                return ChangeImpact(False, summary, "low", reasons)

        return ChangeImpact(False, "Изменения безопасны.", "low", reasons)


class ConflictDetectionAgent:
    def detect_schedule_conflicts(
        self,
        starts: datetime,
        ends: datetime,
        existing: list[tuple[datetime, datetime]],
        min_buffer_minutes: int = 0,
    ) -> ScheduleConflict:
        buffer_minutes = max(min_buffer_minutes, 0)
        conflicts = []
        for item_start, item_end in existing:
            buffered_start = item_start - timedelta(minutes=buffer_minutes)
            buffered_end = item_end + timedelta(minutes=buffer_minutes)
            if starts < buffered_end and ends > buffered_start:
                conflicts.append((item_start, item_end))
        return ScheduleConflict(has_conflict=bool(conflicts), conflicting_ranges=conflicts)


class ScheduleOptimizationAgent:
    def choose_reschedule_slots(self, candidates: list[datetime], timezone: str) -> list[tuple[str, datetime]]:
        if not candidates:
            return []
        nearest = candidates[0]
        comfortable = max(candidates, key=lambda dt: self._comfort_score(dt, timezone))
        optimal = min(candidates, key=lambda dt: self._day_load_minutes(dt, candidates))
        ordered = [
            ("Ближайшее", nearest),
            ("Комфортное", comfortable),
            ("Оптимальное", optimal),
        ]
        unique: list[tuple[str, datetime]] = []
        seen: set[str] = set()
        for label, dt in ordered:
            key = dt.isoformat()
            if key in seen:
                continue
            seen.add(key)
            unique.append((label, dt))
        return unique[:3]

    def _comfort_score(self, dt: datetime, timezone: str) -> int:
        local = dt.astimezone(ZoneInfo(timezone))
        return 1 if 11 <= local.hour <= 19 else 0

    def _day_load_minutes(self, dt: datetime, candidates: list[datetime]) -> int:
        day = dt.date()
        return sum(60 for item in candidates if item.date() == day)


class ReminderPolicyAgent:
    def apply_default_offsets(self, command: ParsedCommand) -> ParsedCommand:
        if isinstance(command, CreateReminderCommand) and not command.remind_offsets:
            command.remind_offsets = [1440, 60, 15, 0]
        return command


class UserMemoryAgent:
    def build_profile(self, user: User) -> UserMemoryProfile:
        offsets = user.extra_data_default_offsets() if hasattr(user, "extra_data_default_offsets") else [1440, 60, 15, 0]
        return UserMemoryProfile(
            timezone=user.timezone,
            locale=user.language,
            default_offsets=offsets,
            work_days=user.work_days,
            time_format_24h=True,
        )

    def to_prompt_context(self, profile: UserMemoryProfile) -> dict[str, Any]:
        return {
            "timezone": profile.timezone,
            "locale": profile.locale,
            "default_offsets": profile.default_offsets,
            "work_days": profile.work_days,
            "time_format_24h": profile.time_format_24h,
        }


class NoteLinkingAgent:
    def link(self, command: ParsedCommand) -> ParsedCommand:
        if (
            isinstance(command, CreateReminderCommand)
            and command.description
            and "чеклист" in command.description.lower()
        ):
            command.description = f"[note-linked] {command.description}"
        return command


class DigestPrioritizationAgent:
    def prioritize(self, lines: list[str]) -> list[str]:
        def score(line: str) -> int:
            low = line.lower()
            if "просроч" in low:
                return 100
            if "дедлайн" in low:
                return 90
            if "урок" in low:
                return 70
            return 10

        return sorted(lines, key=score, reverse=True)


class SummaryAgent:
    def __init__(self, prioritization_agent: DigestPrioritizationAgent) -> None:
        self._prioritizer = prioritization_agent

    def summarize(self, lines: list[str]) -> str:
        if not lines:
            return "Сегодня нет важных событий."
        prioritized = self._prioritizer.prioritize(lines)
        return "Что важно сегодня:\n" + "\n".join(prioritized[:10])


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)
