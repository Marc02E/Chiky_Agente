"""Conditional indexes for 14A retention and 14B stale-running sweep.

Index additions:
- ix_memory_items_expires_at: supports pruning expired memory rows
- ix_evidence_sources_expires_at: supports pruning expired evidence rows
- ix_requests_status_updated_at: supports stale-running scan (status + updated_at)

All are reversible (downgrade drops only the indexes) and additive (no data
alteration). Compatible with SQLite and PostgreSQL.
"""


from alembic import op

revision = "0007_evidence_retention_and_stale_index"
down_revision = "0006_evidence_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_memory_items_expires_at",
        "memory_items",
        ["expires_at"],
    )
    op.create_index(
        "ix_evidence_sources_expires_at",
        "evidence_sources",
        ["expires_at"],
    )
    op.create_index(
        "ix_requests_status_updated_at",
        "requests",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_requests_status_updated_at", table_name="requests"
    )
    op.drop_index(
        "ix_evidence_sources_expires_at", table_name="evidence_sources"
    )
    op.drop_index(
        "ix_memory_items_expires_at", table_name="memory_items"
    )