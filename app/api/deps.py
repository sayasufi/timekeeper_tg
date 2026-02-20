from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.container import AppContainer


def get_container(request: Request) -> AppContainer:
    return cast(AppContainer, request.app.state.container)


async def get_db_session(
    container: AppContainer = Depends(get_container),
) -> AsyncGenerator[AsyncSession, None]:
    async with container.session_factory() as session:
        yield session