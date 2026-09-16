"""FASE X: Add provider_settings table for persistent configuration."""

import sqlalchemy as sa

from alembic import op

revision = "0009_provider_settings"
down_revision = "0008_session_title_and_updated_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_settings",
        sa.Column("user_id", sa.String(200), nullable=False),
        sa.Column("setting_key", sa.String(64), nullable=False),
        sa.Column("setting_value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", "setting_key", name="pk_provider_settings"),
    )
    op.create_index(
        "ix_provider_settings_user_id",
        "provider_settings",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_provider_settings_user_id", table_name="provider_settings")
    op.drop_table("provider_settings")
