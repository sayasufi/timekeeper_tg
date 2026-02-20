from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import cast

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
        await request.app.state.dispatcher.feed_update(bot, update)
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