"""User-scoped persistent evidence sources for governed RAG (FASE 13B).

Reversible and additive: creates the ``evidence_sources`` table so the DB
backed retriever can serve user-scoped evidence with expiry support. Indexed
by user id and created_at for scoped retrieval.
"""

import sqlalchemy as sa

from alembic import op

revision = "0006_evidence_sources"
down_revision = "0005_metric_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence_sources",
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=False),
        sa.Column("uri", sa.String(512), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("authority", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "source_id", name="pk_evidence_sources"),
    )
    op.create_index("ix_evidence_sources_user_id", "evidence_sources", ["user_id"])
    op.create_index("ix_evidence_sources_created_at", "evidence_sources", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_evidence_sources_created_at", table_name="evidence_sources")
    op.drop_index("ix_evidence_sources_user_id", table_name="evidence_sources")
    op.drop_table("evidence_sources")
