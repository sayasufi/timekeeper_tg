from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.interaction_handlers_service import InteractionHandlersService
from app.services.assistant.quick_action_service import QuickActionOutcome


@dataclass
class _FakeUser:
    id: int = 1
    language: str = "ru"
    timezone: str = "UTC"


class _FakeUsers:
    async def get_or_create(self, telegram_id: int, language: str) -> _FakeUser:
        return _FakeUser(language=language)


class _FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeParser:
    def parse_payload(self, payload: dict[str, object]) -> object:
        return object()


class _FakeConfirm:
    def is_batch_confirmation(self, action: str | None, payload: dict[str, object]) -> bool:
        return False


class _FakeMemory:
    def build_profile(self, user: object) -> object:
        return object()


class _FakeState:
    async def get_state(self, telegram_id: int) -> object:
        return object()

    async def build_context_package(self, user: object, state: object, latest_text: str) -> dict[str, object]:
        return {}


class _FakePending:
    async def handle(self, **kwargs: object) -> tuple[AssistantResponse, bool]:
        return AssistantResponse("pending"), False


class _FakeQuick:
    async def handle(self, **kwargs: object) -> QuickActionOutcome:
        return QuickActionOutcome(response=AssistantResponse(""), delegate_text="делегировать")


@pytest.mark.asyncio
async def test_handle_confirmation_cancelled_returns_cancel_message() -> None:
    async def _finalize(user: object, source_text: str | None, response: AssistantResponse) -> AssistantResponse:
        return response

    async def _execute(user: object, command: object) -> AssistantResponse:
        return AssistantResponse("ok")

    async def _execute_batch(
        user: object,
        operations: list[str],
        user_memory: object,
        strategy: str,
        stop_on_error: bool,
    ) -> AssistantResponse:
        return AssistantResponse("batch")

    async def _handle_text(telegram_id: int, text: str, language: str) -> AssistantResponse:
        return AssistantResponse(text)

    service = InteractionHandlersService(
        session=_FakeSession(),  # type: ignore[arg-type]
        users=_FakeUsers(),  # type: ignore[arg-type]
        parser=_FakeParser(),  # type: ignore[arg-type]
        confirmation_service=_FakeConfirm(),  # type: ignore[arg-type]
        memory=_FakeMemory(),  # type: ignore[arg-type]
        conversation_state=_FakeState(),  # type: ignore[arg-type]
        pending_reschedule=_FakePending(),  # type: ignore[arg-type]
        quick_actions=_FakeQuick(),  # type: ignore[arg-type]
        finalize_response=_finalize,
        execute_with_disambiguation=_execute,
        execute_batch_with_args=_execute_batch,
        handle_text=_handle_text,
    )

    result = await service.handle_confirmation(
        telegram_id=1,
        language="ru",
        command_payload={},
        event_id=None,
        confirmed=False,
        confirmation_action=None,
    )

    assert "отменена" in result.text.lower()


@pytest.mark.asyncio
async def test_handle_quick_action_delegates_to_handle_text() -> None:
    async def _finalize(user: object, source_text: str | None, response: AssistantResponse) -> AssistantResponse:
        return response

    async def _execute(user: object, command: object) -> AssistantResponse:
        return AssistantResponse("ok")

    async def _execute_batch(
        user: object,
        operations: list[str],
        user_memory: object,
        strategy: str,
        stop_on_error: bool,
    ) -> AssistantResponse:
        return AssistantResponse("batch")

    captured: dict[str, str] = {}

    async def _handle_text(telegram_id: int, text: str, language: str) -> AssistantResponse:
        captured["text"] = text
        captured["language"] = language
        return AssistantResponse("delegated")

    service = InteractionHandlersService(
        session=_FakeSession(),  # type: ignore[arg-type]
        users=_FakeUsers(),  # type: ignore[arg-type]
        parser=_FakeParser(),  # type: ignore[arg-type]
        confirmation_service=_FakeConfirm(),  # type: ignore[arg-type]
        memory=_FakeMemory(),  # type: ignore[arg-type]
        conversation_state=_FakeState(),  # type: ignore[arg-type]
        pending_reschedule=_FakePending(),  # type: ignore[arg-type]
        quick_actions=_FakeQuick(),  # type: ignore[arg-type]
        finalize_response=_finalize,
        execute_with_disambiguation=_execute,
        execute_batch_with_args=_execute_batch,
        handle_text=_handle_text,
    )

    result = await service.handle_quick_action(
        telegram_id=1,
        language="ru",
        action="send_text_choice",
        payload={"text": "x"},
    )

    assert result.text == "delegated"
    assert captured == {"text": "делегировать", "language": "ru"}

