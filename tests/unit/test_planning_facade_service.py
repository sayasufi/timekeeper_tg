from __future__ import annotations

import pytest

from app.services.assistant.planning_facade_service import PlanningFacadeService
from app.services.smart_agents.models import UserMemoryProfile


class _FakeParser:
    def __init__(self) -> None:
        self.route_calls = 0
        self.risk_calls = 0

    async def route_conversation(self, **kwargs: object) -> tuple[str, list[str], str | None, str | None, str, bool]:
        self.route_calls += 1
        return "commands", ["a"], None, None, "partial_commit", False

    async def assess_plan_risk(self, **kwargs: object) -> tuple[bool, str, str]:
        self.risk_calls += 1
        return False, "low", ""


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.route_calls = 0
        self.risk_calls = 0

    async def route_conversation(self, **kwargs: object) -> tuple[str, list[str], str | None, str | None, str, bool]:
        self.route_calls += 1
        return "answer", [], "ok", None, "partial_commit", False

    async def assess_plan_risk(self, **kwargs: object) -> tuple[bool, str, str]:
        self.risk_calls += 1
        return True, "high", "preview"


@pytest.mark.asyncio
async def test_route_uses_parser_when_memory_is_not_profile() -> None:
    parser = _FakeParser()
    facade = PlanningFacadeService(parser=parser, task_orchestrator=None)  # type: ignore[arg-type]

    mode, _ops, _answer, _question, _strategy, _stop = await facade.route_conversation(
        text="x",
        locale="ru",
        timezone="UTC",
        user_memory={},
        context=None,
    )

    assert mode == "commands"
    assert parser.route_calls == 1


@pytest.mark.asyncio
async def test_route_uses_orchestrator_when_memory_profile_exists() -> None:
    parser = _FakeParser()
    orchestrator = _FakeOrchestrator()
    facade = PlanningFacadeService(parser=parser, task_orchestrator=orchestrator)  # type: ignore[arg-type]
    memory = UserMemoryProfile(
        timezone="UTC",
        locale="ru",
        default_offsets=[0],
        work_days=[1, 2, 3, 4, 5],
        time_format_24h=True,
    )

    mode, _ops, answer, _question, _strategy, _stop = await facade.route_conversation(
        text="x",
        locale="ru",
        timezone="UTC",
        user_memory=memory,
        context=None,
    )

    assert mode == "answer"
    assert answer == "ok"
    assert orchestrator.route_calls == 1
    assert parser.route_calls == 0


@pytest.mark.asyncio
async def test_assess_plan_risk_prefers_orchestrator() -> None:
    parser = _FakeParser()
    orchestrator = _FakeOrchestrator()
    facade = PlanningFacadeService(parser=parser, task_orchestrator=orchestrator)  # type: ignore[arg-type]
    memory = UserMemoryProfile(
        timezone="UTC",
        locale="ru",
        default_offsets=[0],
        work_days=[1, 2, 3, 4, 5],
        time_format_24h=True,
    )

    requires_confirmation, risk_level, preview = await facade.assess_plan_risk(
        text="x",
        operations=["a"],
        locale="ru",
        timezone="UTC",
        user_memory=memory,
        context=None,
    )

    assert requires_confirmation is True
    assert risk_level == "high"
    assert preview == "preview"
    assert orchestrator.risk_calls == 1
    assert parser.risk_calls == 0

