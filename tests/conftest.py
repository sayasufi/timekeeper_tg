from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, prompt: str) -> str:
        return self.content


class FakeSTT:
    def __init__(self, text: str = "test") -> None:
        self.text = text

    async def transcribe(self, audio: bytes, filename: str) -> str:
        return self.text


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    async def incr(self, key: str) -> int:
        current = self._store.get(key, 0)
        value = (int(current) if isinstance(current, int) else 0) + 1
        self._store[key] = value
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def set(self, key: str, value: str, ex: int, nx: bool = False) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def get(self, key: str) -> object | None:
        return self._store.get(key)

    async def delete(self, key: str) -> int:
        if key in self._store:
            del self._store[key]
            return 1
        return 0

    async def ping(self) -> bool:
        return True


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str]] = []

    async def send_message(
        self,
        telegram_id: int,
        text: str,
        buttons: list[tuple[str, str]] | None = None,
    ) -> None:
        self.messages.append((telegram_id, text))

    async def close(self) -> None:
        return None


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        yield session


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def fake_notifier() -> FakeNotifier:
    return FakeNotifier()
