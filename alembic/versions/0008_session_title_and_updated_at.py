"""Add session title and updated timestamp."""

import sqlalchemy as sa
from alembic import op

revision = "0008_session_title_and_updated_at"
down_revision = "0007_evidence_retention_and_stale_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("title", sa.String(200), nullable=True))
    op.add_column(
        "sessions", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sessions", "updated_at")
    op.drop_column("sessions", "title")
