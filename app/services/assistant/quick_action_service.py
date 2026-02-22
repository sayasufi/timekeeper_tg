from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.commands import CreateNoteCommand
from app.domain.enums import Intent
from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.pending_reschedule_service import PendingRescheduleService
from app.services.events.event_service import EventService


@dataclass(slots=True)
class QuickActionOutcome:
    response: AssistantResponse
    should_commit: bool = False
    delegate_text: str | None = None


class QuickActionService:
    def __init__(
        self,
        *,
        events: EventService,
        pending_reschedule: PendingRescheduleService,
    ) -> None:
        self._events = events
        self._pending_reschedule = pending_reschedule

    async def handle(
        self,
        *,
        user: object,
        action: str,
        payload: dict[str, Any],
    ) -> QuickActionOutcome:
        if action == "send_text_choice":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return QuickActionOutcome(
                    response=AssistantResponse("Неверные данные выбора."),
                )
            return QuickActionOutcome(
                response=AssistantResponse(""),
                delegate_text=text,
            )

        if action == "reschedule_pick":
            response, should_commit = await self._pending_reschedule.quick_pick(
                user=user,
                payload=payload,
            )
            return QuickActionOutcome(response=response, should_commit=should_commit)

        if action == "create_renewal_note":
            student_name = payload.get("student_name")
            if not isinstance(student_name, str) or not student_name.strip():
                return QuickActionOutcome(
                    response=AssistantResponse("Не удалось создать запрос на продление."),
                )
            note_cmd = CreateNoteCommand(
                intent=Intent.CREATE_NOTE,
                title=f"Продление предоплаты: {student_name}",
                content=f"Связаться с учеником {student_name} для продления.",
                tags=["billing", "renewal"],
            )
            result = await self._events.create_note(user, note_cmd)
            return QuickActionOutcome(response=AssistantResponse(result), should_commit=True)

        if action == "noop_set_price_hint":
            student_name = payload.get("student_name")
            suffix = f" для {student_name}" if isinstance(student_name, str) and student_name else ""
            return QuickActionOutcome(
                response=AssistantResponse(
                    f"Напишите: 'установи цену занятия{suffix} 2500' или 'измени цену{suffix} на 3000'."
                )
            )

        return QuickActionOutcome(
            response=AssistantResponse("Быстрое действие не поддерживается."),
        )

