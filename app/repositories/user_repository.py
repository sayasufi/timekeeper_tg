from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> User | None:
        stmt = select(User).where(User.id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(self, telegram_id: int, language: str = "ru") -> User:
        user, _created = await self.get_or_create_with_status(telegram_id=telegram_id, language=language)
        return user

    async def get_or_create_with_status(self, telegram_id: int, language: str = "ru") -> tuple[User, bool]:
        existing = await self.get_by_telegram_id(telegram_id)
        if existing is not None:
            if language and existing.language != language:
                existing.language = language
                await self._session.flush()
            return existing, False

        user = User(
            telegram_id=telegram_id,
            language=language or "ru",
            timezone=self._default_timezone_for_language(language),
            work_days=[1, 2, 3, 4, 5],
        )
        try:
            async with self._session.begin_nested():
                self._session.add(user)
                await self._session.flush()
            return user, True
        except IntegrityError:
            # Another concurrent handler created this user first.
            existing = await self.get_by_telegram_id(telegram_id)
            if existing is None:
                raise
            if language and existing.language != language:
                existing.language = language
                await self._session.flush()
            return existing, False

    async def list_all(self) -> list[User]:
        stmt = select(User)
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def update_timezone(self, user: User, timezone: str) -> User:
        user.timezone = timezone
        await self._session.flush()
        return user

    async def update_quiet_hours(self, user: User, start_hhmm: str | None, end_hhmm: str | None) -> User:
        user.quiet_hours_start = start_hhmm
        user.quiet_hours_end = end_hhmm
        await self._session.flush()
        return user

    async def update_work_hours(
        self,
        user: User,
        start_hhmm: str | None,
        end_hhmm: str | None,
        work_days: list[int] | None = None,
    ) -> User:
        user.work_hours_start = start_hhmm
        user.work_hours_end = end_hhmm
        if work_days is not None:
            user.work_days = work_days
        await self._session.flush()
        return user

    async def update_min_buffer(self, user: User, min_buffer_minutes: int) -> User:
        user.min_buffer_minutes = max(0, min_buffer_minutes)
        await self._session.flush()
        return user

    @staticmethod
    def _default_timezone_for_language(language: str | None) -> str:
        if not language:
            return "UTC"
        normalized = language.strip().lower()
        primary = normalized.split("-", 1)[0].split("_", 1)[0]
        if primary == "ru":
            return "Europe/Moscow"
        if primary == "kk":
            return "Asia/Almaty"
        return "UTC"
