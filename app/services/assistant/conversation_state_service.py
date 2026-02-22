from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from app.services.events.event_service import EventService
from app.services.stores.dialog_state_store import DialogState, DialogStateStore

logger = structlog.get_logger(__name__)


class ConversationStateService:
    def __init__(
        self,
        *,
        dialog_state_store: DialogStateStore | None,
        event_service: EventService,
    ) -> None:
        self._dialog_state_store = dialog_state_store
        self._events = event_service

    async def get_state(self, telegram_id: int) -> DialogState:
        if self._dialog_state_store is None:
            return DialogState()
        try:
            return await self._dialog_state_store.get(telegram_id)
        except Exception:
            logger.exception("assistant.dialog_state_read_failed", telegram_id=telegram_id)
            return DialogState()

    async def save_state(
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
        if not state.has_active_scenario():
            state.pending_question = None
            state.pending_reason = None
        try:
            await self._dialog_state_store.save(telegram_id, state)
        except Exception:
            logger.exception("assistant.dialog_state_write_failed", telegram_id=telegram_id)

    async def build_context_package(
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
            "scenario_type": state.scenario_type,
            "scenario_payload": state.scenario_payload,
            "scenario_expires_at": state.scenario_expires_at,
            "latest_user_text": latest_text,
            "backend_state": backend_state,
        }

    def activate_clarification_scenario(self, *, state: DialogState, source_text: str) -> None:
        state.scenario_type = "clarification_followup"
        state.scenario_payload = {"source_text": source_text}
        state.scenario_expires_at = (datetime.now(tz=UTC) + timedelta(hours=24)).isoformat()

    def clear_scenario(self, state: DialogState) -> None:
        state.scenario_type = None
        state.scenario_payload = {}
        state.scenario_expires_at = None

