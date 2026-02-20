from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.routes import router
from app.core.config import Settings
from app.core.container import AppContainer
from app.db.models import AgentRunTrace, Event
from app.domain.enums import EventType
from app.repositories.agent_run_trace_repository import AgentRunTraceRepository
from app.repositories.event_repository import EventRepository
from app.repositories.user_repository import UserRepository


class DummyLLM:
    async def complete(self, prompt: str) -> str:
        return "{}"


class DummySTT:
    async def transcribe(self, audio: bytes, filename: str) -> str:
        return "text"


class DummyNotifier:
    async def send_message(
        self,
        telegram_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_health_live_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: Any,
    tmp_path: Path,
) -> None:
    settings = Settings(TELEGRAM_BOT_TOKEN="test", EXPORT_DIR=tmp_path)
    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        redis=fake_redis,
        llm_client=DummyLLM(),
        stt_client=DummySTT(),
        notifier=DummyNotifier(),
    )

    app = FastAPI()
    app.include_router(router)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_ready_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: Any,
    tmp_path: Path,
) -> None:
    settings = Settings(TELEGRAM_BOT_TOKEN="test", EXPORT_DIR=tmp_path)
    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        redis=fake_redis,
        llm_client=DummyLLM(),
        stt_client=DummySTT(),
        notifier=DummyNotifier(),
    )

    app = FastAPI()
    app.include_router(router)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_export_user_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: Any,
    tmp_path: Path,
) -> None:
    settings = Settings(TELEGRAM_BOT_TOKEN="test", EXPORT_DIR=tmp_path)
    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        redis=fake_redis,
        llm_client=DummyLLM(),
        stt_client=DummySTT(),
        notifier=DummyNotifier(),
    )

    async with session_factory() as session:
        users = UserRepository(session)
        events = EventRepository(session)
        user = await users.get_or_create(telegram_id=555, language="ru")
        await events.create(
            Event(
                user_id=user.id,
                event_type=EventType.REMINDER.value,
                title="Test",
                starts_at=datetime(2026, 2, 20, 10, 0, tzinfo=UTC),
                rrule=None,
                remind_offsets=[0],
                extra_data={},
            )
        )
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/users/555/export")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["user"]["telegram_id"] == 555
    assert Path(body["snapshot_path"]).exists()


@pytest.mark.asyncio
async def test_agent_quality_endpoint(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: Any,
    tmp_path: Path,
) -> None:
    settings = Settings(TELEGRAM_BOT_TOKEN="test", EXPORT_DIR=tmp_path)
    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        redis=fake_redis,
        llm_client=DummyLLM(),
        stt_client=DummySTT(),
        notifier=DummyNotifier(),
    )

    async with session_factory() as session:
        users = UserRepository(session)
        traces = AgentRunTraceRepository(session)
        user = await users.get_or_create(telegram_id=777, language="ru")
        await traces.create(
            AgentRunTrace(
                user_id=user.id,
                source="parser",
                input_text="test",
                locale="ru",
                timezone="UTC",
                route_mode="precise",
                result_intent="clarify",
                confidence=0.4,
                selected_path=["clarify"],
                stages=[],
                total_duration_ms=11,
            )
        )
        await session.commit()

    app = FastAPI()
    app.include_router(router)
    app.state.container = container

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/admin/agent-quality", params={"telegram_id": 777, "days": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["metrics"]["clarification_rate"] >= 0.0
