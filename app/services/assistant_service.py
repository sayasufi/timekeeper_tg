from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.domain.enums import EventType, Intent
from app.repositories.user_repository import UserRepository
from app.services.assistant_response import (
    AmbiguityOption,
    AmbiguityRequest,
    AssistantResponse,
    ConfirmationRequest,
    QuickAction,
)
from app.services.bot_response_service import BotResponseService
from app.services.command_parser_service import CommandParserService
from app.services.dialog_state_store import DialogState, DialogStateStore
from app.services.event_service import EventService
from app.services.smart_agents import (
    ChangeImpactAgent,
    EventDisambiguationAgent,
    UserMemoryAgent,
)

logger = structlog.get_logger(__name__)


class AssistantService:
    def __init__(
        self,
        session: AsyncSession,
        user_repository: UserRepository,
        parser_service: CommandParserService,
        event_service: EventService,
        response_renderer: BotResponseService | None = None,
        dialog_state_store: DialogStateStore | None = None,
    ) -> None:
        self._session = session
        self._users = user_repository
        self._parser = parser_service
        self._events = event_service
        self._response_renderer = response_renderer
        self._dialog_state_store = dialog_state_store
        self._disambiguation = EventDisambiguationAgent()
        self._impact = ChangeImpactAgent()
        self._memory = UserMemoryAgent()

    async def handle_text(self, telegram_id: int, text: str, language: str) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        user_memory = self._memory.build_profile(user)
        dialog_state = await self._get_dialog_state(telegram_id)
        context_package = await self._build_context_package(user=user, state=dialog_state, latest_text=text)

        try:
            mode, operations, answer, question, execution_strategy, stop_on_error = await self._parser.route_conversation(
                text=text,
                locale=user.language,
                timezone=user.timezone,
                user_memory=user_memory,
                context=context_package,
            )
            if mode == "clarify":
                clarify_text = question or await self._ask_clarification(
                    user=user,
                    source_text=text,
                    reason="Не хватает данных для безопасного выполнения запроса.",
                    fallback="Уточните, пожалуйста, запрос.",
                    user_memory=user_memory,
                    context=context_package,
                )
                dialog_state.pending_question = clarify_text
                dialog_state.pending_reason = "missing_required_data"
                await self._save_dialog_state(
                    telegram_id=telegram_id,
                    state=dialog_state,
                    user_text=text,
                    assistant_text=clarify_text,
                )
                response = AssistantResponse(clarify_text)
                await self._session.commit()
                return await self._finalize_response(user=user, source_text=text, response=response)
            if mode == "answer":
                await self._session.commit()
                answer_text = answer or await self._parser.render_policy_text(
                    kind="answer_fallback",
                    source_text=text,
                    reason="assistant_answer_missing",
                    locale=user.language,
                    timezone=user.timezone,
                    fallback="Могу помочь по функциям бота и расписанию. Сформулируйте вопрос чуть точнее.",
                    user_memory=user_memory,
                    context=context_package,
                )
                response = AssistantResponse(answer_text, metadata={"handled_by": "conversation_manager"})
                dialog_state.pending_question = None
                dialog_state.pending_reason = None
                await self._save_dialog_state(
                    telegram_id=telegram_id,
                    state=dialog_state,
                    user_text=text,
                    assistant_text=answer_text,
                )
                return await self._finalize_response(user=user, source_text=text, response=response)

            if len(operations) > 1:
                batch_response = await self._handle_batch_operations(
                    user=user,
                    operations=operations,
                    user_memory=user_memory,
                    execution_strategy=execution_strategy,
                    stop_on_error=stop_on_error,
                )
                await self._session.commit()
                await self._save_dialog_state(
                    telegram_id=telegram_id,
                    state=dialog_state,
                    user_text=text,
                    assistant_text=batch_response.text,
                )
                return await self._finalize_response(user=user, source_text=text, response=batch_response)

            command = await self._parser.parse(
                text=text,
                locale=user.language,
                timezone=user.timezone,
                user_id=user.id,
                user_memory=user_memory,
                context=context_package,
            )
            response = await self._execute_with_disambiguation(
                user=user,
                command=command,
                bypass_confirmation=False,
            )
            await self._session.commit()
            await self._save_dialog_state(
                telegram_id=telegram_id,
                state=dialog_state,
                user_text=text,
                assistant_text=response.text,
            )
            return await self._finalize_response(user=user, source_text=text, response=response)
        except Exception as exc:
            await self._session.rollback()
            logger.exception("assistant.handle_text_failed", telegram_id=telegram_id, error=str(exc))
            response = AssistantResponse(
                await self._ask_clarification(
                    user=user,
                    source_text=text,
                    reason=f"Внутренняя ошибка: {exc}",
                    fallback="Произошла ошибка обработки запроса. Попробуйте еще раз.",
                    user_memory=user_memory,
                    context=context_package,
                )
            )
            await self._save_dialog_state(
                telegram_id=telegram_id,
                state=dialog_state,
                user_text=text,
                assistant_text=response.text,
            )
            return await self._finalize_response(user=user, source_text=text, response=response)

    async def handle_resolution(
        self,
        telegram_id: int,
        language: str,
        command_payload: dict[str, Any],
        selected_event_id: UUID,
    ) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        command = self._parser.parse_payload(command_payload)

        if isinstance(command, UpdateReminderCommand | DeleteReminderCommand | UpdateScheduleCommand):
            command.event_id = selected_event_id
            command.search_text = None

        response = await self._execute_with_disambiguation(
            user=user,
            command=command,
            bypass_confirmation=False,
        )
        await self._session.commit()
        return await self._finalize_response(user=user, source_text=None, response=response)

    async def handle_confirmation(
        self,
        telegram_id: int,
        language: str,
        command_payload: dict[str, Any],
        event_id: UUID | None,
        confirmed: bool,
    ) -> AssistantResponse:
        if not confirmed:
            user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
            response = AssistantResponse("Операция отменена.")
            return await self._finalize_response(user=user, source_text=None, response=response)

        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        command = self._parser.parse_payload(command_payload)

        if event_id is not None and isinstance(command, UpdateReminderCommand | DeleteReminderCommand | UpdateScheduleCommand):
            command.event_id = event_id
            command.search_text = None

        response = await self._execute_with_disambiguation(
            user=user,
            command=command,
            bypass_confirmation=True,
        )
        await self._session.commit()
        return await self._finalize_response(user=user, source_text=None, response=response)

    async def handle_pending_reschedule(
        self,
        telegram_id: int,
        language: str,
        event_id: UUID,
        text: str,
    ) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        user_memory = self._memory.build_profile(user)
        context_package = await self._build_context_package(
            user=user,
            state=await self._get_dialog_state(telegram_id),
            latest_text=text,
        )
        command = await self._parser.parse(
            text=text,
            locale=user.language,
            timezone=user.timezone,
            user_id=user.id,
            user_memory=user_memory,
            context=context_package,
        )
        if not isinstance(command, UpdateScheduleCommand):
            clarification = await self._ask_clarification(
                user=user,
                source_text=text,
                reason="Ожидался перенос урока, но в сообщении нет однозначных параметров переноса.",
                fallback="Уточните перенос: на какую дату и время перенести занятие?",
                user_memory=user_memory,
                context=context_package,
            )
            response = AssistantResponse(
                clarification,
                metadata={"pending_keep": True},
            )
            return await self._finalize_response(user=user, source_text=text, response=response)
        if command.apply_scope is None:
            clarification = await self._ask_clarification(
                user=user,
                source_text=text,
                reason="Не указан scope переноса: разово или для всей серии.",
                fallback="Уточните, как применить перенос: только на этой неделе или навсегда в расписании?",
                user_memory=user_memory,
                context=context_package,
            )
            response = AssistantResponse(
                clarification,
                metadata={"pending_keep": True},
            )
            return await self._finalize_response(user=user, source_text=text, response=response)

        command.event_id = event_id
        command.search_text = None
        if command.apply_scope == "single_week" and not (command.new_date or command.new_time):
            if command.weekday and command.time:
                command.apply_scope = "series"
            else:
                target = await self._events.get_target_event(
                    user_id=user.id,
                    event_id=event_id,
                    search_text=None,
                    allowed_types={EventType.LESSON.value},
                )
                if target is not None:
                    suggestions = await self._events.suggest_reschedule_slots_v2(user=user, event=target)
                    if suggestions:
                        actions = []
                        for kind, item in suggestions:
                            local = item.astimezone(ZoneInfo(user.timezone))
                            actions.append(
                                QuickAction(
                                    label=f"{kind}: {local.strftime('%a %d.%m %H:%M')}",
                                    action="reschedule_pick",
                                    payload={
                                        "event_id": str(target.id),
                                        "new_date": local.strftime("%Y-%m-%d"),
                                        "new_time": local.strftime("%H:%M"),
                                        "apply_scope": "single_week",
                                    },
                                )
                            )
                        response = AssistantResponse(
                            "Не хватает новой даты/времени. Выберите один из свободных слотов:",
                            quick_actions=actions,
                            metadata={"pending_keep": True},
                        )
                        return await self._finalize_response(user=user, source_text=text, response=response)
                response = AssistantResponse(
                    await self._ask_clarification(
                        user=user,
                        source_text=text,
                        reason="Не удалось подобрать слот без явной новой даты/времени.",
                        fallback="Уточните перенос: на какую дату и время перенести занятие?",
                        user_memory=user_memory,
                        context=context_package,
                    ),
                    metadata={"pending_keep": True},
                )
                return await self._finalize_response(user=user, source_text=text, response=response)

        text_result = await self._events.update_schedule(user, command)
        await self._session.commit()
        response = AssistantResponse(text_result)
        return await self._finalize_response(user=user, source_text=text, response=response)

    async def handle_quick_action(
        self,
        telegram_id: int,
        language: str,
        action: str,
        payload: dict[str, Any],
    ) -> AssistantResponse:
        user = await self._users.get_or_create(telegram_id=telegram_id, language=language)
        if action == "reschedule_pick":
            event_id_raw = payload.get("event_id")
            if not isinstance(event_id_raw, str):
                return AssistantResponse("Неверные данные быстрого действия.")
            try:
                event_id = UUID(event_id_raw)
            except ValueError:
                return AssistantResponse("Неверные данные быстрого действия.")
            command = UpdateScheduleCommand(
                intent=Intent.UPDATE_SCHEDULE,
                event_id=event_id,
                new_date=str(payload.get("new_date")) if payload.get("new_date") is not None else None,
                new_time=str(payload.get("new_time")) if payload.get("new_time") is not None else None,
                apply_scope=(
                    str(payload.get("apply_scope"))
                    if payload.get("apply_scope") in {"single_week", "series"}
                    else "single_week"
                ),
            )
            result = await self._events.update_schedule(user, command)
            await self._session.commit()
            response = AssistantResponse(result)
            return await self._finalize_response(user=user, source_text=None, response=response)
        if action == "create_renewal_note":
            student_name = payload.get("student_name")
            if not isinstance(student_name, str) or not student_name.strip():
                response = AssistantResponse("Не удалось создать запрос на продление.")
                return await self._finalize_response(user=user, source_text=None, response=response)
            note_cmd = CreateNoteCommand(
                intent=Intent.CREATE_NOTE,
                title=f"Продление предоплаты: {student_name}",
                content=f"Связаться с учеником {student_name} для продления.",
                tags=["billing", "renewal"],
            )
            result = await self._events.create_note(user, note_cmd)
            await self._session.commit()
            response = AssistantResponse(result)
            return await self._finalize_response(user=user, source_text=None, response=response)
        if action == "noop_set_price_hint":
            student_name = payload.get("student_name")
            suffix = f" для {student_name}" if isinstance(student_name, str) and student_name else ""
            response = AssistantResponse(
                f"Напишите: 'установи цену занятия{suffix} 2500' или 'измени цену{suffix} на 3000'."
            )
            return await self._finalize_response(user=user, source_text=None, response=response)
        response = AssistantResponse("Быстрое действие не поддерживается.")
        return await self._finalize_response(user=user, source_text=None, response=response)

    async def _execute_with_disambiguation(
        self,
        user: object,
        command: ParsedCommand,
        bypass_confirmation: bool,
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

            if not bypass_confirmation:
                confirmation = await self._build_confirmation(
                    user=user,
                    command=command,
                    allowed_types={EventType.REMINDER.value, EventType.BIRTHDAY.value, EventType.LESSON.value},
                )
                if confirmation is not None:
                    return AssistantResponse("Подтвердите изменение события.", confirmation=confirmation)

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

            if not bypass_confirmation:
                confirmation = await self._build_confirmation(
                    user=user,
                    command=command,
                    allowed_types={EventType.REMINDER.value, EventType.BIRTHDAY.value, EventType.LESSON.value},
                )
                if confirmation is not None:
                    return AssistantResponse("Подтвердите удаление.", confirmation=confirmation)

            return AssistantResponse(await self._events.delete_reminder(user, command))

        if isinstance(command, UpdateScheduleCommand):
            if self._needs_schedule_scope_clarification(command):
                return AssistantResponse(
                    await self._ask_clarification(
                        user=user,
                        source_text=command.model_dump_json(),
                        reason="Для изменения расписания не хватает scope применения изменений.",
                        fallback="Уточните перенос: применить только на этой неделе или изменить расписание навсегда?",
                        user_memory=self._memory.build_profile(user),
                        context={},
                    )
                )
            if command.bulk_cancel_weekday and command.bulk_cancel_scope and not bypass_confirmation:
                scope_text = (
                    "на следующей неделе"
                    if command.bulk_cancel_scope == "next_week"
                    else "во всех будущих неделях"
                )
                return AssistantResponse(
                    "Подтвердите массовую операцию.",
                    confirmation=ConfirmationRequest(
                        action=command.intent.value,
                        command_payload=command.model_dump(mode="json"),
                        event_id=None,
                        summary=f"Будут отменены уроки {command.bulk_cancel_weekday} {scope_text}.",
                    ),
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

            if not bypass_confirmation:
                confirmation = await self._build_confirmation(
                    user=user,
                    command=command,
                    allowed_types={EventType.LESSON.value},
                )
                if confirmation is not None:
                    return AssistantResponse("Подтвердите изменение урока.", confirmation=confirmation)

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
                        actions = []
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
            if not bypass_confirmation:
                preview = self._build_schedule_preview(command)
                return AssistantResponse(
                    "Проверьте импорт расписания. Подтвердить применение?",
                    confirmation=ConfirmationRequest(
                        action=command.intent.value,
                        command_payload=command.model_dump(mode="json"),
                        event_id=None,
                        summary=preview,
                    ),
                )
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
                return self._with_payment_hints(
                    result_text,
                    student_name=command.search_text,
                )
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
            confirm_payload = {
                "intent": Intent.MARK_LESSON_PAID.value,
                "event_id": None,
                "search_text": student_name,
                "amount": 0,
                "prepaid_lessons_add": None,
                "prepaid_lessons_set": None,
                "payment_total": amount,
            }
            return AssistantResponse(
                "Подтвердите зачисление перевода.",
                confirmation=ConfirmationRequest(
                    action=Intent.MARK_LESSON_PAID.value,
                    command_payload=confirm_payload,
                    event_id=None,
                    summary=preview,
                ),
            )

        return AssistantResponse(await self._execute(user=user, command=command))

    def _needs_schedule_scope_clarification(self, command: UpdateScheduleCommand) -> bool:
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

    async def _build_confirmation(
        self,
        user: object,
        command: ParsedCommand,
        allowed_types: set[str],
    ) -> ConfirmationRequest | None:
        from app.db.models import User

        if not isinstance(user, User):
            return None

        target = await self._events.get_target_event(
            user_id=user.id,
            event_id=getattr(command, "event_id", None),
            search_text=getattr(command, "search_text", None),
            allowed_types=allowed_types,
        )
        if target is None:
            return None

        impact = self._impact.build(command, target)
        if not impact.requires_confirmation:
            return None

        reasons = ", ".join(impact.reasons) if impact.reasons else "без доп. факторов"
        return ConfirmationRequest(
            action=command.intent.value,
            command_payload=command.model_dump(mode="json"),
            event_id=target.id,
            summary=f"{impact.summary} Риск: {impact.risk_level}. Причины: {reasons}.",
        )

    def _build_schedule_preview(self, command: CreateScheduleCommand) -> str:
        if not command.slots:
            template = command.template or "без шаблона"
            return f"Импорт по шаблону: {template}."
        lines = ["Будут созданы уроки:"]
        for slot in command.slots[:12]:
            student = slot.student_name or slot.subject or "Ученик"
            lines.append(f"- {slot.weekday} {slot.time} • {student} ({slot.duration_minutes}м)")
        if len(command.slots) > 12:
            lines.append(f"... и еще {len(command.slots) - 12}")
        return "\n".join(lines)

    async def _execute(self, user: object, command: ParsedCommand) -> str:
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

    async def _finalize_response(self, user: object, source_text: str | None, response: AssistantResponse) -> AssistantResponse:
        from app.db.models import User

        if not isinstance(user, User):
            return response
        if self._response_renderer is None:
            return response

        response.text = await self._response_renderer.render_for_user(
            user=user,
            raw_text=response.text,
            response_kind=self._response_kind(response),
            user_text=source_text,
        )
        if response.confirmation is not None:
            response.confirmation.summary = await self._response_renderer.render_for_user(
                user=user,
                raw_text=response.confirmation.summary,
                response_kind="confirmation_summary",
                user_text=source_text,
            )
        if response.quick_actions:
            for item in response.quick_actions:
                item.label = await self._response_renderer.render_for_user(
                    user=user,
                    raw_text=item.label,
                    response_kind="button_label",
                    user_text=source_text,
                )
        return response

    def _response_kind(self, response: AssistantResponse) -> str:
        if response.ambiguity is not None:
            return "ambiguity_choice"
        if response.confirmation is not None:
            return "confirmation_request"
        if "уточните" in response.text.lower():
            return "clarification_question"
        return "regular_reply"

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

    async def _handle_batch_operations(
        self,
        user: object,
        operations: list[str],
        user_memory: object,
        execution_strategy: str = "partial_commit",
        stop_on_error: bool = False,
    ) -> AssistantResponse:
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user_memory, UserMemoryProfile):
            return AssistantResponse("Не удалось обработать пакет операций.")
        from app.db.models import User

        if not isinstance(user, User):
            return AssistantResponse("Не удалось обработать пакет операций.")

        executed_texts: list[str] = []
        all_or_nothing = execution_strategy == "all_or_nothing"
        failed = False
        for index, item in enumerate(operations, start=1):
            savepoint = await self._session.begin_nested() if all_or_nothing else None
            response = await self._execute_batch_item(
                user=user,
                user_memory=user_memory,
                original_text="\n".join(operations),
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
            if self._is_batch_failure_response(response.text):
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

    async def _execute_batch_item(
        self,
        user: object,
        user_memory: object,
        original_text: str,
        item: str,
    ) -> AssistantResponse | None:
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user_memory, UserMemoryProfile):
            return AssistantResponse("Не удалось обработать шаг.")
        from app.db.models import User

        if not isinstance(user, User):
            return AssistantResponse("Не удалось обработать шаг.")

        attempt_text = item
        for _attempt in range(2):
            try:
                batch_context = self._batch_context(original_text=original_text, item_text=attempt_text)
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

            return await self._execute_with_disambiguation(user=user, command=command, bypass_confirmation=False)

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

    async def _ask_clarification(
        self,
        *,
        user: object,
        source_text: str,
        reason: str,
        fallback: str,
        user_memory: object,
        context: dict[str, object] | None = None,
    ) -> str:
        from app.db.models import User
        from app.services.smart_agents.models import UserMemoryProfile

        if not isinstance(user, User):
            return fallback
        if not isinstance(user_memory, UserMemoryProfile):
            return fallback
        return await self._parser.generate_clarification(
            text=source_text,
            reason=reason,
            locale=user.language,
            timezone=user.timezone,
            fallback=fallback,
            user_memory=user_memory,
            context=context,
        )

    async def _get_dialog_state(self, telegram_id: int) -> DialogState:
        if self._dialog_state_store is None:
            return DialogState()
        try:
            return await self._dialog_state_store.get(telegram_id)
        except Exception:
            logger.exception("assistant.dialog_state_read_failed", telegram_id=telegram_id)
            return DialogState()

    async def _save_dialog_state(
        self,
        *,
        telegram_id: int,
        state: DialogState,
        user_text: str,
        assistant_text: str,
    ) -> None:
        if self._dialog_state_store is None:
            return
        state.turns.append({"role": "user", "content": user_text})
        state.turns.append({"role": "assistant", "content": assistant_text})
        if "уточните" not in assistant_text.lower():
            state.pending_question = None
            state.pending_reason = None
        try:
            await self._dialog_state_store.save(telegram_id, state)
        except Exception:
            logger.exception("assistant.dialog_state_write_failed", telegram_id=telegram_id)

    async def _build_context_package(
        self,
        *,
        user: object,
        state: DialogState,
        latest_text: str,
    ) -> dict[str, object]:
        from app.db.models import User

        if not isinstance(user, User):
            return {}
        try:
            backend_state = await self._events.compact_user_context(user)
        except Exception:
            logger.exception("assistant.backend_context_failed", user_id=user.id)
            backend_state = {}
        return {
            "dialog_history": state.turns[-6:],
            "pending_question": state.pending_question,
            "pending_reason": state.pending_reason,
            "latest_user_text": latest_text,
            "backend_state": backend_state,
        }

    def _batch_context(self, *, original_text: str, item_text: str) -> dict[str, object]:
        return {
            "batch_original_text": original_text,
            "batch_item": item_text,
        }

    def _is_batch_failure_response(self, text: str) -> bool:
        low = text.lower()
        return low.startswith("не удалось") or low.startswith("ошибка")
