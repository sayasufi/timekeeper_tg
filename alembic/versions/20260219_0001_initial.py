"""initial schema

Revision ID: 20260219_0001
Revises:
Create Date: 2026-02-19 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260219_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="ru"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"])

    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rrule", sa.Text(), nullable=True),
        sa.Column("remind_offsets", sa.JSON(), nullable=False),
        sa.Column("extra_data", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_user_id", "events", ["user_id"])
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_starts_at", "events", ["starts_at"])
    op.create_index("ix_events_user_type", "events", ["user_id", "event_type"])
    op.create_index("ix_events_user_active", "events", ["user_id", "is_active"])

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offset_minutes", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "occurrence_at", "offset_minutes", name="uq_notification_unique"),
    )
    op.create_index("ix_notification_logs_user_id", "notification_logs", ["user_id"])
    op.create_index("ix_notification_logs_event_id", "notification_logs", ["event_id"])
    op.create_index("ix_notification_logs_occurrence_at", "notification_logs", ["occurrence_at"])


def downgrade() -> None:
    op.drop_index("ix_notification_logs_occurrence_at", table_name="notification_logs")
    op.drop_index("ix_notification_logs_event_id", table_name="notification_logs")
    op.drop_index("ix_notification_logs_user_id", table_name="notification_logs")
    op.drop_table("notification_logs")

    op.drop_index("ix_events_user_active", table_name="events")
    op.drop_index("ix_events_user_type", table_name="events")
    op.drop_index("ix_events_starts_at", table_name="events")
    op.drop_index("ix_events_event_type", table_name="events")
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_table("events")

    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
