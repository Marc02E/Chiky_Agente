"""Add updated_at to requests (F-03 stale-running recovery basis)."""

import sqlalchemy as sa

from alembic import op

revision = "0004_request_updated_at"
down_revision = "0003_user_scoped_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "requests",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_column("requests", "updated_at")