"""add notes table

Revision ID: 20260220_0004
Revises: 20260220_0003
Create Date: 2026-02-20 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0004"
down_revision: str | None = "20260220_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("linked_event_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["linked_event_id"], ["events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notes_user_id", "notes", ["user_id"])
    op.create_index("ix_notes_linked_event_id", "notes", ["linked_event_id"])
    op.create_index("ix_notes_user_active", "notes", ["user_id", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_notes_user_active", table_name="notes")
    op.drop_index("ix_notes_linked_event_id", table_name="notes")
    op.drop_index("ix_notes_user_id", table_name="notes")
    op.drop_table("notes")
