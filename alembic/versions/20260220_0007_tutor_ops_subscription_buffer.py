"""add tutor operations fields: buffer and subscription counters

Revision ID: 20260220_0007
Revises: 20260220_0006
Create Date: 2026-02-20 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0007"
down_revision: str | None = "20260220_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("min_buffer_minutes", sa.Integer(), nullable=False, server_default="15"))

    op.add_column("students", sa.Column("canceled_by_tutor_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("students", sa.Column("canceled_by_student_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("students", sa.Column("subscription_total_lessons", sa.Integer(), nullable=True))
    op.add_column("students", sa.Column("subscription_remaining_lessons", sa.Integer(), nullable=True))
    op.add_column("students", sa.Column("subscription_price", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("students", "subscription_price")
    op.drop_column("students", "subscription_remaining_lessons")
    op.drop_column("students", "subscription_total_lessons")
    op.drop_column("students", "canceled_by_student_count")
    op.drop_column("students", "canceled_by_tutor_count")
    op.drop_column("users", "min_buffer_minutes")
