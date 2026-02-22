from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.commands import UpdateScheduleCommand
from app.domain.enums import EventType, Intent
from app.services.assistant.assistant_response import AssistantResponse, QuickAction
from app.services.events.event_service import EventService
from app.services.parser.command_parser_service import CommandParserService

AskClarificationFn = Callable[..., Awaitable[str]]


class PendingRescheduleService:
    def __init__(
        self,
        *,
        parser: CommandParserService,
        events: EventService,
        ask_clarification: AskClarificationFn,
    ) -> None:
        self._parser = parser
        self._events = events
        self._ask_clarification = ask_clarification

    async def handle(
        self,
        *,
        user: object,
        event_id: UUID,
        text: str,
        user_memory: object,
        context_package: dict[str, object],
    ) -> tuple[AssistantResponse, bool]:
        from app.db.models import User

        if not isinstance(user, User):
            return AssistantResponse("Не удалось обработать перенос."), False

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
            return AssistantResponse(clarification, metadata={"pending_keep": True}), False

        if command.apply_scope is None:
            clarification = await self._ask_clarification(
                user=user,
                source_text=text,
                reason="Не указан scope переноса: разово или для всей серии.",
                fallback="Уточните, как применить перенос: только на этой неделе или навсегда в расписании?",
                user_memory=user_memory,
                context=context_package,
            )
            return AssistantResponse(clarification, metadata={"pending_keep": True}), False

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
                        actions: list[QuickAction] = []
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
                        return (
                            AssistantResponse(
                                "Не хватает новой даты/времени. Выберите один из свободных слотов:",
                                quick_actions=actions,
                                metadata={"pending_keep": True},
                            ),
                            False,
                        )
                clarification = await self._ask_clarification(
                    user=user,
                    source_text=text,
                    reason="Не удалось подобрать слот без явной новой даты/времени.",
                    fallback="Уточните перенос: на какую дату и время перенести занятие?",
                    user_memory=user_memory,
                    context=context_package,
                )
                return AssistantResponse(clarification, metadata={"pending_keep": True}), False

        text_result = await self._events.update_schedule(user, command)
        return AssistantResponse(text_result), True

    async def quick_pick(
        self,
        *,
        user: object,
        payload: dict[str, Any],
    ) -> tuple[AssistantResponse, bool]:
        from app.db.models import User

        if not isinstance(user, User):
            return AssistantResponse("Неверные данные быстрого действия."), False

        event_id_raw = payload.get("event_id")
        if not isinstance(event_id_raw, str):
            return AssistantResponse("Неверные данные быстрого действия."), False
        try:
            event_id = UUID(event_id_raw)
        except ValueError:
            return AssistantResponse("Неверные данные быстрого действия."), False
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
        return AssistantResponse(result), True
