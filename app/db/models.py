from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    quiet_hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    work_hours_start: Mapped[str | None] = mapped_column(String(5), nullable=True)
    work_hours_end: Mapped[str | None] = mapped_column(String(5), nullable=True)
    work_days: Mapped[list[int]] = mapped_column(JSON, default=list)
    min_buffer_minutes: Mapped[int] = mapped_column(Integer, default=15)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    events: Mapped[list[Event]] = relationship(back_populates="user", cascade="all,delete-orphan")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rrule: Mapped[str | None] = mapped_column(Text, nullable=True)
    remind_offsets: Mapped[list[int]] = mapped_column(JSON, default=list)
    extra_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[User] = relationship(back_populates="events")


class NotificationLog(Base):
    __tablename__ = "notification_logs"
    __table_args__ = (
        UniqueConstraint("event_id", "occurrence_at", "offset_minutes", name="uq_notification_unique"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    occurrence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    offset_minutes: Mapped[int] = mapped_column(Integer)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DueNotification(Base):
    __tablename__ = "due_notifications"
    __table_args__ = (
        UniqueConstraint("event_id", "occurrence_at", "offset_minutes", name="uq_due_notification"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    occurrence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    offset_minutes: Mapped[int] = mapped_column(Integer)
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentRunTrace(Base):
    __tablename__ = "agent_run_traces"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    source: Mapped[str] = mapped_column(String(32), default="parser")
    input_text: Mapped[str] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(8))
    timezone: Mapped[str] = mapped_column(String(64))
    route_mode: Mapped[str] = mapped_column(String(16), default="precise", index=True)
    result_intent: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column()
    selected_path: Mapped[list[str]] = mapped_column(JSON, default=list)
    stages: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    total_duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    linked_event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class Student(Base):
    __tablename__ = "students"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_status: Mapped[str] = mapped_column(String(32), default="unknown")
    total_paid_amount: Mapped[int] = mapped_column(Integer, default=0)
    missed_lessons_count: Mapped[int] = mapped_column(Integer, default=0)
    canceled_by_tutor_count: Mapped[int] = mapped_column(Integer, default=0)
    canceled_by_student_count: Mapped[int] = mapped_column(Integer, default=0)
    subscription_total_lessons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subscription_remaining_lessons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subscription_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_lesson_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    goal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    weekly_frequency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    preferred_slots: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_lesson_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("students.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    amount: Mapped[int] = mapped_column(Integer, default=0)
    prepaid_lessons_delta: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


Index("ix_events_user_type", Event.user_id, Event.event_type)
Index("ix_events_user_active", Event.user_id, Event.is_active)
Index("ix_due_status_trigger", DueNotification.status, DueNotification.trigger_at)
Index("ix_outbox_status_available", OutboxMessage.status, OutboxMessage.available_at)
Index("ix_agent_trace_user_created", AgentRunTrace.user_id, AgentRunTrace.created_at)
Index("ix_notes_user_active", Note.user_id, Note.is_active)
Index("ix_students_user_active", Student.user_id, Student.is_active)
Index("ix_paytx_user_created", PaymentTransaction.user_id, PaymentTransaction.created_at)
