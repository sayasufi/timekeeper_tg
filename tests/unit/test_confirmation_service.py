from __future__ import annotations

import pytest

from app.services.assistant.assistant_response import AssistantResponse
from app.services.assistant.confirmation_service import ConfirmationService


@pytest.mark.asyncio
async def test_confirmation_service_runs_batch_executor() -> None:
    service = ConfirmationService()

    async def execute_batch(
        user: object,
        operations: list[str],
        user_memory: object,
        execution_strategy: str,
        stop_on_error: bool,
    ) -> AssistantResponse:
        assert user == 42
        assert user_memory == {"m": 1}
        assert operations == ["a", "b"]
        assert execution_strategy == "all_or_nothing"
        assert stop_on_error is True
        return AssistantResponse("ok")

    response = await service.run_batch_confirmation(
        user=42,
        user_memory={"m": 1},
        command_payload={
            "operations": ["a", "b"],
            "execution_strategy": "all_or_nothing",
            "stop_on_error": True,
        },
        execute_batch=execute_batch,
    )

    assert response.text == "ok"


@pytest.mark.asyncio
async def test_confirmation_service_handles_invalid_operations_payload() -> None:
    service = ConfirmationService()

    async def execute_batch(
        user: object,
        operations: list[str],
        user_memory: object,
        execution_strategy: str,
        stop_on_error: bool,
    ) -> AssistantResponse:
        return AssistantResponse("should_not_be_called")

    response = await service.run_batch_confirmation(
        user=1,
        user_memory={},
        command_payload={"operations": "bad"},
        execute_batch=execute_batch,
    )

    assert "не удалось" in response.text.lower()


def test_confirmation_service_detects_batch_confirmation() -> None:
    service = ConfirmationService()
    assert service.is_batch_confirmation("batch_execute", {}) is True
    assert service.is_batch_confirmation(None, {"__kind": "batch_plan"}) is True
    assert service.is_batch_confirmation(None, {}) is False

