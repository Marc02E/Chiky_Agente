"""User-scoped idempotency keys (F-01 hardening)."""

from alembic import op

revision = "0003_user_scoped_idempotency"
down_revision = "0002_memory_and_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("requests") as batch_op:
        batch_op.drop_constraint("uq_requests_idempotency_key", type_="unique")
        batch_op.create_unique_constraint(
            "uq_requests_user_id_idempotency_key",
            ["user_id", "idempotency_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("requests") as batch_op:
        batch_op.drop_constraint(
            "uq_requests_user_id_idempotency_key", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_requests_idempotency_key", ["idempotency_key"]
        )