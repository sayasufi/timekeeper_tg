from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.commands import (
    ClarifyCommand,
    CreateBirthdayCommand,
    CreateNoteCommand,
    CreateReminderCommand,
    CreateScheduleCommand,
    CreateStudentCommand,
    DeleteNoteCommand,
    DeleteReminderCommand,
    DeleteStudentCommand,
    ListEventsCommand,
    ListNotesCommand,
    MarkLessonMissedCommand,
    MarkLessonPaidCommand,
    ParseBankTransferCommand,
    ParsedCommand,
    StudentCardCommand,
    TutorReportCommand,
    UpdateNoteCommand,
    UpdateReminderCommand,
    UpdateScheduleCommand,
    UpdateSettingsCommand,
    UpdateStudentCommand,
)
from app.domain.enums import EventType
from app.repositories.user_repository import UserRepository
from app.services.assistant.assistant_response import (
    AmbiguityOption,
    AmbiguityRequest,
    AssistantResponse,
    QuickAction,
)
from app.services.events.event_service import EventService
from app.services.smart_agents import EventDisambiguationAgent, UserMemoryAgent

AskClarificationFn = Callable[..., Awaitable[str]]


class CommandExecutionService:
    def __init__(
        self,
        *,
        users: UserRepository,
        events: EventService,
        ask_clarification: AskClarificationFn,
        disambiguation: EventDisambiguationAgent | None = None,
        memory: UserMemoryAgent | None = None,
    ) -> None:
        self._users = users
        self._events = events
        self._ask_clarification = ask_clarification
        self._disambiguation = disambiguation or EventDisambiguationAgent()
        self._memory = memory or UserMemoryAgent()

    async def execute_with_disambiguation(
        self,
        *,
        user: object,
        command: ParsedCommand,
    ) -> AssistantResponse:
        from app.db.models import User

        if not isinstance(user, User):
            msg = "Unexpected user type"
            raise TypeError(msg)

        if isinstance(command, UpdateReminderCommand):
            ambiguity = await self._resolve_ambiguity(
                user=user,
                search_text=command.search_text,
                event_id=command.event_id,
                allowed_types={EventType.REMINDER.value, EventType.BIRTHDAY.value, EventType.LESSON.value},
                command_payload=command.model_dump(mode="json"),
            )
            if ambiguity is not None:
                return AssistantResponse("Нашел несколько событий. Выберите нужное:", ambiguity=ambiguity)
            return AssistantResponse(await self._events.update_reminder(user, command))

        if isinstance(command, DeleteReminderCommand):
            ambiguity = await self._resolve_ambiguity(
                user=user,
                search_text=command.search_text,
                event_id=command.event_id,
                allowed_types={EventType.REMINDER.value, EventType.BIRTHDAY.value, EventType.LESSON.value},
                command_payload=command.model_dump(mode="json"),
            )
            if ambiguity is not None:
                return AssistantResponse("Нашел несколько событий. Какое удалить?", ambiguity=ambiguity)
            return AssistantResponse(await self._events.delete_reminder(user, command))

        if isinstance(command, UpdateScheduleCommand):
            if self.needs_schedule_scope_clarification(command):
                return AssistantResponse(
                    await self._ask_clarification(
                        user=user,
                        source_text=command.model_dump_json(),
                        reason="Для изменения расписания не хватает scope применения изменений.",
                        fallback=(
                            "Уточните перенос: применить только на этой неделе "
                            "или изменить расписание навсегда?"
                        ),
                        user_memory=self._memory.build_profile(user),
                        context={},
                    )
                )

            ambiguity = await self._resolve_ambiguity(
                user=user,
                search_text=command.search_text,
                event_id=command.event_id,
                allowed_types={EventType.LESSON.value},
                command_payload=command.model_dump(mode="json"),
            )
            if ambiguity is not None:
                return AssistantResponse("Нашел несколько уроков. Выберите нужный:", ambiguity=ambiguity)

            if not command.new_date and not command.new_time:
                event = await self._events.get_target_event(
                    user_id=user.id,
                    event_id=command.event_id,
                    search_text=command.search_text,
                    allowed_types={EventType.LESSON.value},
                )
                if event is not None:
                    suggestions = await self._events.suggest_reschedule_slots_v2(user=user, event=event)
                    if suggestions:
                        actions: list[QuickAction] = []
                        for kind, item in suggestions:
                            local = item.astimezone(ZoneInfo(user.timezone))
                            actions.append(
                                QuickAction(
                                    label=f"{kind}: {local.strftime('%a %d.%m %H:%M')}",
                                    action="reschedule_pick",
                                    payload={
                                        "event_id": str(event.id),
                                        "new_date": local.strftime("%Y-%m-%d"),
                                        "new_time": local.strftime("%H:%M"),
                                        "apply_scope": command.apply_scope or "single_week",
                                    },
                                )
                            )
                        return AssistantResponse(
                            "Выберите новый слот для переноса (ближайший/комфортный/оптимальный).",
                            quick_actions=actions,
                            metadata={"pending_keep": True},
                        )
            return AssistantResponse(await self._events.update_schedule(user, command))

        if isinstance(command, CreateScheduleCommand):
            return AssistantResponse(await self._events.create_schedule(user, command))

        if isinstance(command, MarkLessonPaidCommand):
            student_balance_only = (
                command.event_id is None
                and command.search_text is not None
                and (
                    command.prepaid_lessons_add is not None
                    or command.prepaid_lessons_set is not None
                    or command.payment_total is not None
                )
            )
            if student_balance_only:
                result_text = await self._events.mark_lesson_paid(
                    user,
                    event_id=None,
                    search_text=command.search_text,
                    amount=command.amount,
                    prepaid_lessons_add=command.prepaid_lessons_add,
                    prepaid_lessons_set=command.prepaid_lessons_set,
                    payment_total=command.payment_total,
                )
                return self._with_payment_hints(result_text, student_name=command.search_text)

            ambiguity = await self._resolve_ambiguity(
                user=user,
                search_text=command.search_text,
                event_id=command.event_id,
                allowed_types={EventType.LESSON.value},
                command_payload=command.model_dump(mode="json"),
            )
            if ambiguity is not None:
                return AssistantResponse("Нашел несколько уроков. Выберите для отметки оплаты:", ambiguity=ambiguity)

            result_text = await self._events.mark_lesson_paid(
                user,
                command.event_id,
                search_text=command.search_text,
                amount=command.amount,
                prepaid_lessons_add=command.prepaid_lessons_add,
                prepaid_lessons_set=command.prepaid_lessons_set,
                payment_total=command.payment_total,
            )
            return self._with_payment_hints(result_text, student_name=command.search_text)

        if isinstance(command, MarkLessonMissedCommand):
            ambiguity = await self._resolve_ambiguity(
                user=user,
                search_text=command.search_text,
                event_id=command.event_id,
                allowed_types={EventType.LESSON.value},
                command_payload=command.model_dump(mode="json"),
            )
            if ambiguity is not None:
                return AssistantResponse("Нашел несколько уроков. Выберите, где отметить пропуск:", ambiguity=ambiguity)
            return AssistantResponse(await self._events.mark_lesson_missed(user, command.event_id))

        if isinstance(command, UpdateStudentCommand):
            return AssistantResponse(await self._events.update_student(user, command))

        if isinstance(command, CreateStudentCommand):
            return AssistantResponse(await self._events.create_student(user, command))

        if isinstance(command, DeleteStudentCommand):
            return AssistantResponse(await self._events.delete_student(user, command))

        if isinstance(command, StudentCardCommand):
            return AssistantResponse(await self._events.student_card(user, command))

        if isinstance(command, ParseBankTransferCommand):
            preview, student_name, amount = await self._events.parse_bank_transfer(user, command)
            if student_name is None or amount is None:
                return AssistantResponse(preview)
            result_text = await self._events.mark_lesson_paid(
                user,
                event_id=None,
                search_text=student_name,
                amount=0,
                prepaid_lessons_add=None,
                prepaid_lessons_set=None,
                payment_total=amount,
            )
            return self._with_payment_hints(f"{preview}\n\n{result_text}", student_name=student_name)

        return AssistantResponse(await self.execute(user=user, command=command))

    async def execute(self, *, user: object, command: ParsedCommand) -> str:
        from app.db.models import User

        if not isinstance(user, User):
            msg = "Unexpected user type"
            raise TypeError(msg)

        if isinstance(command, ClarifyCommand):
            return command.question

        if isinstance(command, CreateReminderCommand):
            if not command.start_at:
                return "Уточните, когда напомнить."
            return await self._events.create_reminder(user, command)

        if isinstance(command, UpdateReminderCommand):
            return await self._events.update_reminder(user, command)

        if isinstance(command, DeleteReminderCommand):
            return await self._events.delete_reminder(user, command)

        if isinstance(command, ListEventsCommand):
            return await self._events.list_events(user, command)

        if isinstance(command, CreateScheduleCommand):
            return await self._events.create_schedule(user, command)

        if isinstance(command, UpdateScheduleCommand):
            return await self._events.update_schedule(user, command)

        if isinstance(command, MarkLessonPaidCommand):
            return await self._events.mark_lesson_paid(
                user,
                command.event_id,
                search_text=command.search_text,
                amount=command.amount,
                prepaid_lessons_add=command.prepaid_lessons_add,
                prepaid_lessons_set=command.prepaid_lessons_set,
                payment_total=command.payment_total,
            )

        if isinstance(command, MarkLessonMissedCommand):
            return await self._events.mark_lesson_missed(user, command.event_id)

        if isinstance(command, UpdateSettingsCommand):
            if command.timezone:
                await self._users.update_timezone(user, command.timezone)
            if command.quiet_off:
                await self._users.update_quiet_hours(user, None, None)
            elif command.quiet_start and command.quiet_end:
                await self._users.update_quiet_hours(user, command.quiet_start, command.quiet_end)
            if command.work_off:
                await self._users.update_work_hours(user, None, None)
            elif command.work_start and command.work_end:
                await self._users.update_work_hours(user, command.work_start, command.work_end, command.work_days)
            if command.min_buffer_minutes is not None:
                await self._users.update_min_buffer(user, command.min_buffer_minutes)
            return "Настройки обновлены."

        if isinstance(command, UpdateStudentCommand):
            return await self._events.update_student(user, command)

        if isinstance(command, CreateStudentCommand):
            return await self._events.create_student(user, command)

        if isinstance(command, DeleteStudentCommand):
            return await self._events.delete_student(user, command)

        if isinstance(command, StudentCardCommand):
            return await self._events.student_card(user, command)

        if isinstance(command, ParseBankTransferCommand):
            preview, _student_name, _amount = await self._events.parse_bank_transfer(user, command)
            return preview

        if isinstance(command, TutorReportCommand):
            now_local = datetime.now(tz=UTC).astimezone(ZoneInfo(user.timezone))
            if command.report_type == "today":
                return await self._events.tutor_day_report(user=user, day=now_local.date())
            if command.report_type == "tomorrow":
                return await self._events.tutor_day_report(user=user, day=(now_local.date() + timedelta(days=1)))
            if command.report_type == "finance_week":
                return await self._events.tutor_finance_report(user=user, period_days=7)
            if command.report_type == "finance_month":
                return await self._events.tutor_finance_report(user=user, period_days=30)
            if command.report_type == "attendance_week":
                return await self._events.tutor_attendance_log(user=user, period_days=7)
            if command.report_type == "attendance_month":
                return await self._events.tutor_attendance_log(user=user, period_days=30)
            return await self._events.tutor_missed_report(user=user)

        if isinstance(command, CreateBirthdayCommand):
            return await self._events.create_birthday(user, command)

        if isinstance(command, CreateNoteCommand):
            return await self._events.create_note(user, command)

        if isinstance(command, UpdateNoteCommand):
            return await self._events.update_note(user, command)

        if isinstance(command, DeleteNoteCommand):
            return await self._events.delete_note(user, command)

        if isinstance(command, ListNotesCommand):
            return await self._events.list_notes(user, command)

        return "Не удалось выполнить команду."

    def needs_schedule_scope_clarification(self, command: UpdateScheduleCommand) -> bool:
        if command.apply_scope is not None:
            return False

        has_schedule_change = any(
            [
                command.new_date is not None,
                command.new_time is not None,
                command.weekday is not None,
                command.time is not None,
                command.duration_minutes is not None,
                command.delete,
            ]
        )
        return has_schedule_change

    async def _resolve_ambiguity(
        self,
        *,
        user: object,
        search_text: str | None,
        event_id: UUID | None,
        allowed_types: set[str],
        command_payload: dict[str, Any],
    ) -> AmbiguityRequest | None:
        from app.db.models import User

        if not isinstance(user, User):
            return None
        if event_id is not None:
            return None
        if not search_text:
            return None

        candidates = await self._events.find_candidates(
            user_id=user.id,
            search_text=search_text,
            allowed_types=allowed_types,
        )
        if len(candidates) <= 1:
            return None

        ranked = self._disambiguation.rank(
            search_text=search_text,
            candidates=candidates,
            timezone=user.timezone,
        )
        options = [
            AmbiguityOption(
                event_id=UUID(item.event_id),
                title=item.label,
                subtitle=item.subtitle,
            )
            for item in ranked
        ]
        return AmbiguityRequest(
            action=str(command_payload.get("intent", "")),
            command_payload=command_payload,
            options=options,
        )

    def _with_payment_hints(self, text: str, student_name: str | None) -> AssistantResponse:
        actions = [
            QuickAction(
                label="Выставить цену занятия",
                action="noop_set_price_hint",
                payload={"student_name": student_name or ""},
            )
        ]
        if "осталось 1 занятие" in text.lower():
            actions.append(
                QuickAction(
                    label="Создать запрос на продление",
                    action="create_renewal_note",
                    payload={"student_name": student_name or ""},
                )
            )
        return AssistantResponse(text=text, quick_actions=actions)
