from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import structlog
from aiogram import Bot
from aiogram.types import Update
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.contextvars import bound_contextvars

from app.api.deps import get_container, get_db_session
from app.core.container import AppContainer
from app.core.security import IdempotencyGuard
from app.repositories.agent_run_trace_repository import AgentRunTraceRepository
from app.repositories.outbox_repository import OutboxRepository
from app.repositories.user_repository import UserRepository

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(
    container: AppContainer = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
        ping_result = container.redis.ping()
        if inspect.isawaitable(ping_result):
            await ping_result
    except Exception as exc:
        logger.exception("health.ready_failed", error=str(exc))
        raise HTTPException(status_code=503, detail="dependencies unavailable") from exc
    return {"status": "ready"}


@router.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, str]:
    container = cast(AppContainer, request.app.state.container)
    bot = cast(Bot, request.app.state.bot)

    if container.settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != container.settings.telegram_webhook_secret:
            raise HTTPException(status_code=401, detail="invalid webhook secret")

    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": bot})

    guard = IdempotencyGuard(container.redis)
    is_new = await guard.check_and_set(f"tg_update:{update.update_id}")
    if not is_new:
        return {"status": "duplicate"}

    with bound_contextvars(tg_update_id=update.update_id):
        logger.info("webhook.telegram_update_received")
        task = asyncio.create_task(_process_telegram_update(request.app, bot, update))
        tasks = getattr(request.app.state, "telegram_update_tasks", None)
        if tasks is None:
            tasks = set()
            request.app.state.telegram_update_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return {"status": "ok"}


@router.get("/admin/users/{telegram_id}/export")
async def export_user(
    telegram_id: int,
    container: AppContainer = Depends(get_container),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    service = container.create_export_service(session)
    try:
        path, payload = await service.export_user(telegram_id)
        await session.commit()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "snapshot_path": str(path),
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "data": payload,
    }


@router.get("/admin/agent-quality")
async def agent_quality(
    days: int = 7,
    telegram_id: int | None = None,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    user_id: int | None = None
    if telegram_id is not None:
        users = UserRepository(session)
        user = await users.get_by_telegram_id(telegram_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        user_id = user.id
    repo = AgentRunTraceRepository(session)
    metrics = await repo.quality_snapshot(days=days, user_id=user_id)
    return {"days": days, "telegram_id": telegram_id, "metrics": metrics}


@router.post("/admin/outbox/{outbox_id}/requeue")
async def requeue_outbox(
    outbox_id: UUID,
    available_in_seconds: int = 0,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, object]:
    repo = OutboxRepository(session)
    item = await repo.get_by_id(outbox_id)
    if item is None:
        raise HTTPException(status_code=404, detail="outbox item not found")
    available_at = datetime.now(tz=UTC)
    if available_in_seconds > 0:
        from datetime import timedelta

        available_at = available_at + timedelta(seconds=available_in_seconds)
    await repo.requeue(item, available_at=available_at)
    await session.commit()
    return {"status": "ok", "outbox_id": str(outbox_id), "available_at": available_at.isoformat()}


async def _process_telegram_update(app: Any, bot: Bot, update: Update) -> None:
    semaphore = getattr(app.state, "telegram_update_semaphore", None)
    try:
        if semaphore is None:
            await app.state.dispatcher.feed_update(bot, update)
            return
        async with semaphore:
            await app.state.dispatcher.feed_update(bot, update)
    except Exception as exc:
        logger.exception("webhook.telegram_update_failed", update_id=update.update_id, error=str(exc))
