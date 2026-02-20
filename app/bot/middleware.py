from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.container import AppContainer
from app.core.security import FloodControl


class ContainerMiddleware(BaseMiddleware):
    def __init__(self, container: AppContainer) -> None:
        self._container = container

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["container"] = self._container
        return await handler(event, data)


class DatabaseSessionMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            return await handler(event, data)


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, flood_control: FloodControl) -> None:
        self._flood_control = flood_control

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user is not None:
            allowed = await self._flood_control.allow(event.from_user.id)
            if not allowed:
                await event.answer("Слишком много запросов. Подождите немного.")
                return None

        return await handler(event, data)
