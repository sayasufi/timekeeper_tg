"""add students table

Revision ID: 20260220_0005
Revises: 20260220_0004
Create Date: 2026-02-20 02:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0005"
down_revision: str | None = "20260220_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "students",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_students_user_id", "students", ["user_id"])
    op.create_index("ix_students_name", "students", ["name"])
    op.create_index("ix_students_user_active", "students", ["user_id", "is_active"])


def downgrade() -> None:
    op.drop_index("ix_students_user_active", table_name="students")
    op.drop_index("ix_students_name", table_name="students")
    op.drop_index("ix_students_user_id", table_name="students")
    op.drop_table("students")
