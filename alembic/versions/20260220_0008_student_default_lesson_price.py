"""add default lesson price to students

Revision ID: 20260220_0008
Revises: 20260220_0007
Create Date: 2026-02-20 05:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0008"
down_revision: str | None = "20260220_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("students", sa.Column("default_lesson_price", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("students", "default_lesson_price")
