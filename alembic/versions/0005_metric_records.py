"""Shared multi-worker metric aggregation table (N-01)."""

import sqlalchemy as sa

from alembic import op

revision = "0005_metric_records"
down_revision = "0004_request_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metric_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("metric_type", sa.String(16), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("worker_id", "metric_name", name="uq_metric_records_worker_name"),
    )
    op.create_index("ix_metric_records_worker_id", "metric_records", ["worker_id"])
    op.create_index("ix_metric_records_updated_at", "metric_records", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_metric_records_updated_at", table_name="metric_records")
    op.drop_index("ix_metric_records_worker_id", table_name="metric_records")
    op.drop_table("metric_records")