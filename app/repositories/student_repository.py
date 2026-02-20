from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Student


class StudentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create_by_name(self, user_id: int, name: str) -> Student:
        stmt = select(Student).where(
            Student.user_id == user_id,
            Student.is_active.is_(True),
            Student.name.ilike(name),
        )
        result = await self._session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        student = Student(user_id=user_id, name=name)
        self._session.add(student)
        await self._session.flush()
        return student

    async def list_for_user(self, user_id: int) -> list[Student]:
        stmt = (
            select(Student)
            .where(Student.user_id == user_id, Student.is_active.is_(True))
            .order_by(Student.name)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars())

    async def get_for_user_by_id(self, user_id: int, student_id: UUID) -> Student | None:
        stmt = select(Student).where(
            Student.user_id == user_id,
            Student.id == student_id,
            Student.is_active.is_(True),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_by_name(self, user_id: int, name: str) -> Student | None:
        stmt = select(Student).where(
            Student.user_id == user_id,
            Student.is_active.is_(True),
            Student.name.ilike(name),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, student: Student) -> Student:
        await self._session.flush()
        return student
