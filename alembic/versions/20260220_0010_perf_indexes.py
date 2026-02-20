"""add performance indexes for schedule and ledger queries

Revision ID: 20260220_0010
Revises: 20260220_0009
Create Date: 2026-02-20 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260220_0010"
down_revision: str | None = "20260220_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_events_user_starts_at", "events", ["user_id", "starts_at"])
    op.create_index("ix_paytx_student_created", "payment_transactions", ["student_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_paytx_student_created", table_name="payment_transactions")
    op.drop_index("ix_events_user_starts_at", table_name="events")
