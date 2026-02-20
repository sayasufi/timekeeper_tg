"""add payment ledger and student crm fields

Revision ID: 20260220_0009
Revises: 20260220_0008
Create Date: 2026-02-20 06:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0009"
down_revision: str | None = "20260220_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("students", sa.Column("status", sa.String(length=16), nullable=False, server_default="active"))
    op.add_column("students", sa.Column("goal", sa.String(length=255), nullable=True))
    op.add_column("students", sa.Column("level", sa.String(length=64), nullable=True))
    op.add_column("students", sa.Column("weekly_frequency", sa.Integer(), nullable=True))
    op.add_column("students", sa.Column("preferred_slots", sa.JSON(), nullable=False, server_default="[]"))

    op.create_table(
        "payment_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=True),
        sa.Column("event_id", sa.Uuid(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prepaid_lessons_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paytx_user_created", "payment_transactions", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_paytx_user_created", table_name="payment_transactions")
    op.drop_table("payment_transactions")

    op.drop_column("students", "preferred_slots")
    op.drop_column("students", "weekly_frequency")
    op.drop_column("students", "level")
    op.drop_column("students", "goal")
    op.drop_column("students", "status")
