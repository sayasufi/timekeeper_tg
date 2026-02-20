"""add agent run traces

Revision ID: 20260220_0003
Revises: 20260220_0002
Create Date: 2026-02-20 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260220_0003"
down_revision: str | None = "20260220_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_run_traces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="parser"),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("locale", sa.String(length=8), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("route_mode", sa.String(length=16), nullable=False, server_default="precise"),
        sa.Column("result_intent", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("selected_path", sa.JSON(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("total_duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_run_traces_user_id", "agent_run_traces", ["user_id"])
    op.create_index("ix_agent_run_traces_route_mode", "agent_run_traces", ["route_mode"])
    op.create_index("ix_agent_run_traces_result_intent", "agent_run_traces", ["result_intent"])
    op.create_index("ix_agent_trace_user_created", "agent_run_traces", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_agent_trace_user_created", table_name="agent_run_traces")
    op.drop_index("ix_agent_run_traces_result_intent", table_name="agent_run_traces")
    op.drop_index("ix_agent_run_traces_route_mode", table_name="agent_run_traces")
    op.drop_index("ix_agent_run_traces_user_id", table_name="agent_run_traces")
    op.drop_table("agent_run_traces")
