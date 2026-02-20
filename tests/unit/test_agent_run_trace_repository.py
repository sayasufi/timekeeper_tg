from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AgentRunTrace
from app.repositories.agent_run_trace_repository import AgentRunTraceRepository


@pytest.mark.asyncio
async def test_quality_snapshot_metrics(db_session: AsyncSession) -> None:
    repo = AgentRunTraceRepository(db_session)
    now = datetime.now(tz=UTC)
    traces = [
        AgentRunTrace(
            user_id=1,
            source="parser",
            input_text="a",
            locale="ru",
            timezone="UTC",
            route_mode="precise",
            result_intent="create_reminder",
            confidence=0.9,
            selected_path=["ok"],
            stages=[],
            total_duration_ms=20,
            created_at=now,
        ),
        AgentRunTrace(
            user_id=1,
            source="parser",
            input_text="b",
            locale="ru",
            timezone="UTC",
            route_mode="precise",
            result_intent="clarify",
            confidence=0.4,
            selected_path=["clarify"],
            stages=[],
            total_duration_ms=30,
            created_at=now,
        ),
        AgentRunTrace(
            user_id=1,
            source="parser",
            input_text="c",
            locale="ru",
            timezone="UTC",
            route_mode="precise",
            result_intent="update_schedule",
            confidence=0.3,
            selected_path=["ok"],
            stages=[],
            total_duration_ms=40,
            created_at=now,
        ),
    ]
    for item in traces:
        await repo.create(item)
    await db_session.commit()

    metrics = await repo.quality_snapshot(days=7, user_id=1)
    assert metrics["parse_success"] == 0.6667
    assert metrics["clarification_rate"] == 0.3333
    assert metrics["wrong_action_rate"] == 0.3333
