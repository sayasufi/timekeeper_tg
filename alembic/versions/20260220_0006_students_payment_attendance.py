"""extend students with payment and attendance fields

Revision ID: 20260220_0006
Revises: 20260220_0005
Create Date: 2026-02-20 03:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0006"
down_revision: str | None = "20260220_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("students", sa.Column("payment_status", sa.String(length=32), nullable=False, server_default="unknown"))
    op.add_column("students", sa.Column("total_paid_amount", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("students", sa.Column("missed_lessons_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("students", sa.Column("last_lesson_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("students", "last_lesson_at")
    op.drop_column("students", "missed_lessons_count")
    op.drop_column("students", "total_paid_amount")
    op.drop_column("students", "payment_status")
