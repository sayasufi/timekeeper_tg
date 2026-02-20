from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Note


class NoteRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, note: Note) -> Note:
        self._session.add(note)
        await self._session.flush()
        return note

    async def get_for_user(self, user_id: int, note_id: UUID) -> Note | None:
        stmt = select(Note).where(Note.id == note_id, Note.user_id == user_id, Note.is_active.is_(True))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_first(self, user_id: int, search_text: str) -> Note | None:
        stmt = (
            select(Note)
            .where(
                Note.user_id == user_id,
                Note.is_active.is_(True),
                (Note.title.ilike(f"%{search_text}%") | Note.content.ilike(f"%{search_text}%")),
            )
            .order_by(Note.updated_at.desc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_for_user(self, user_id: int, search_text: str | None = None) -> list[Note]:
        stmt = select(Note).where(Note.user_id == user_id, Note.is_active.is_(True))
        if search_text:
            stmt = stmt.where(Note.title.ilike(f"%{search_text}%") | Note.content.ilike(f"%{search_text}%"))
        result = await self._session.execute(stmt.order_by(Note.updated_at.desc()))
        return list(result.scalars())

    async def update(self, note: Note) -> Note:
        await self._session.flush()
        return note

    async def soft_delete(self, note: Note) -> None:
        note.is_active = False
        await self._session.flush()
