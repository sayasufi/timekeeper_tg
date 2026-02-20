from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.routes import router as api_router
from app.bot.factory import create_dispatcher
from app.core.config import get_settings
from app.core.container import AppContainer
from app.core.logging import setup_logging
from app.db.session import create_engine, create_session_factory
from app.integrations.llm.client import HTTPLLMClient
from app.integrations.stt.client import HTTPSTTClient
from app.integrations.telegram.notifier import TelegramNotifier


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url, decode_responses=False)

    llm_client = HTTPLLMClient(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
    stt_client = HTTPSTTClient(base_url=settings.stt_base_url, api_key=settings.stt_api_key)
    notifier = TelegramNotifier(bot_token=settings.telegram_bot_token)

    container = AppContainer(
        settings=settings,
        session_factory=session_factory,
        redis=redis,
        llm_client=llm_client,
        stt_client=stt_client,
        notifier=notifier,
    )

    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = create_dispatcher(container)

    app.state.engine = engine
    app.state.container = container
    app.state.bot = bot
    app.state.dispatcher = dispatcher

    if settings.telegram_webhook_url:
        await bot.set_webhook(
            url=settings.telegram_webhook_url,
            secret_token=settings.telegram_webhook_secret or None,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )

    try:
        yield
    finally:
        if settings.telegram_webhook_url:
            await bot.delete_webhook(drop_pending_updates=False)

        await bot.session.close()
        await container.notifier.close()
        await redis.aclose()
        await engine.dispose()


app = FastAPI(title="TimeKeeper", lifespan=lifespan)
app.include_router(api_router)