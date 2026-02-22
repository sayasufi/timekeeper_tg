from __future__ import annotations

import pytest

from app.db.models import User
from app.services.assistant.conversation_state_service import ConversationStateService
from app.services.stores.dialog_state_store import DialogState


class _FakeStore:
    def __init__(self) -> None:
        self.state = DialogState()

    async def get(self, telegram_id: int) -> DialogState:
        return self.state

    async def save(self, telegram_id: int, state: DialogState) -> None:
        self.state = state


class _FakeEventService:
    async def compact_user_context(self, user: User) -> dict[str, object]:
        return {"user_id": user.id, "timezone": user.timezone}


@pytest.mark.asyncio
async def test_save_state_clears_pending_when_scenario_is_inactive() -> None:
    store = _FakeStore()
    service = ConversationStateService(
        dialog_state_store=store,  # type: ignore[arg-type]
        event_service=_FakeEventService(),  # type: ignore[arg-type]
    )
    state = DialogState(pending_question="q", pending_reason="r")

    await service.save_state(
        telegram_id=1,
        state=state,
        user_text="u",
        assistant_text="a",
    )

    assert store.state.pending_question is None
    assert store.state.pending_reason is None
    assert len(store.state.turns) == 2


@pytest.mark.asyncio
async def test_build_context_package_includes_backend_state() -> None:
    service = ConversationStateService(
        dialog_state_store=None,
        event_service=_FakeEventService(),  # type: ignore[arg-type]
    )
    user = User(telegram_id=1007, language="ru", timezone="UTC")
    user.id = 7
    state = DialogState(turns=[{"role": "user", "content": "hello"}])

    context = await service.build_context_package(
        user=user,
        state=state,
        latest_text="latest",
    )

    assert context["latest_user_text"] == "latest"
    assert context["dialog_history"] == [{"role": "user", "content": "hello"}]
    assert context["backend_state"] == {"user_id": 7, "timezone": "UTC"}

