"""Initial Phase 1 schema."""

import sqlalchemy as sa

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "requests",
        sa.Column("request_id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("input", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="accepted"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_requests_idempotency_key"),
    )
    op.create_index("ix_requests_session_id", "requests", ["session_id"])
    op.create_index("ix_requests_user_id", "requests", ["user_id"])
    op.create_index("ix_requests_correlation_id", "requests", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_requests_correlation_id", table_name="requests")
    op.drop_index("ix_requests_user_id", table_name="requests")
    op.drop_index("ix_requests_session_id", table_name="requests")
    op.drop_table("requests")
    op.drop_index("ix_sessions_user_id", table_name="sessions")
    op.drop_table("sessions")
