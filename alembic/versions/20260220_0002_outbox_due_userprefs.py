"""add outbox due index and user preferences

Revision ID: 20260220_0002
Revises: 20260219_0001
Create Date: 2026-02-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0002"
down_revision: str | None = "20260219_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("quiet_hours_start", sa.String(length=5), nullable=True))
    op.add_column("users", sa.Column("quiet_hours_end", sa.String(length=5), nullable=True))
    op.add_column("users", sa.Column("work_hours_start", sa.String(length=5), nullable=True))
    op.add_column("users", sa.Column("work_hours_end", sa.String(length=5), nullable=True))
    op.add_column("users", sa.Column("work_days", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))

    op.create_table(
        "due_notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("offset_minutes", sa.Integer(), nullable=False),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "occurrence_at", "offset_minutes", name="uq_due_notification"),
    )
    op.create_index("ix_due_notifications_user_id", "due_notifications", ["user_id"])
    op.create_index("ix_due_notifications_event_id", "due_notifications", ["event_id"])
    op.create_index("ix_due_notifications_occurrence_at", "due_notifications", ["occurrence_at"])
    op.create_index("ix_due_notifications_trigger_at", "due_notifications", ["trigger_at"])
    op.create_index("ix_due_notifications_status", "due_notifications", ["status"])
    op.create_index("ix_due_status_trigger", "due_notifications", ["status", "trigger_at"])

    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False, server_default="telegram"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_outbox_messages_user_id", "outbox_messages", ["user_id"])
    op.create_index("ix_outbox_messages_status", "outbox_messages", ["status"])
    op.create_index("ix_outbox_messages_available_at", "outbox_messages", ["available_at"])
    op.create_index("ix_outbox_status_available", "outbox_messages", ["status", "available_at"])


def downgrade() -> None:
    op.drop_index("ix_outbox_status_available", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_available_at", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_status", table_name="outbox_messages")
    op.drop_index("ix_outbox_messages_user_id", table_name="outbox_messages")
    op.drop_table("outbox_messages")

    op.drop_index("ix_due_status_trigger", table_name="due_notifications")
    op.drop_index("ix_due_notifications_status", table_name="due_notifications")
    op.drop_index("ix_due_notifications_trigger_at", table_name="due_notifications")
    op.drop_index("ix_due_notifications_occurrence_at", table_name="due_notifications")
    op.drop_index("ix_due_notifications_event_id", table_name="due_notifications")
    op.drop_index("ix_due_notifications_user_id", table_name="due_notifications")
    op.drop_table("due_notifications")

    op.drop_column("users", "work_days")
    op.drop_column("users", "work_hours_end")
    op.drop_column("users", "work_hours_start")
    op.drop_column("users", "quiet_hours_end")
    op.drop_column("users", "quiet_hours_start")