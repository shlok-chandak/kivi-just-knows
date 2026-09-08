"""memories: claim_key, live-claim uniqueness, valid_until

Revision ID: e4b7c1d93a52
Revises: c8d2f5a41b69
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b7c1d93a52"
down_revision = "c8d2f5a41b69"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows have no claim identity to recover -- the old id was a hash
    # keyed on a conversation, which is not the same thing. Seeding from the
    # primary key keeps the constraint satisfiable without inventing matches
    # between claims that were never compared.
    op.add_column("memories", sa.Column("claim_key", sa.Text(), nullable=True))
    op.execute("UPDATE memories SET claim_key = id::text WHERE claim_key IS NULL")
    op.alter_column("memories", "claim_key", nullable=False)

    op.create_index(
        "uq_memories_live_claim",
        "memories",
        ["user_id", "claim_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'superseded'"),
    )

    op.alter_column("memories", "valid_to", new_column_name="valid_until")
    op.drop_column("memories", "valid_from")

    # Two statuses become unreachable, so anything sitting in them is folded
    # into the one that has a rule behind it.
    op.execute(
        "UPDATE memories SET status = 'superseded' "
        "WHERE status IN ('invalidated', 'expired')"
    )


def downgrade() -> None:
    op.add_column(
        "memories", sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column("memories", "valid_until", new_column_name="valid_to")
    op.drop_index("uq_memories_live_claim", table_name="memories")
    op.drop_column("memories", "claim_key")
