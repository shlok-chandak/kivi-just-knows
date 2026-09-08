"""rejected_candidates: record where and when, for refusals with no event

Revision ID: f7a3d0c62e18
Revises: e4b7c1d93a52
"""

import sqlalchemy as sa
from alembic import op

revision = "f7a3d0c62e18"
down_revision = "e4b7c1d93a52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A dictation refused at ingest is never stored, so the refusal row cannot
    # borrow its app or timestamp from an event. It carries its own.
    op.add_column("rejected_candidates", sa.Column("app", sa.Text(), nullable=True))
    op.add_column(
        "rejected_candidates",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rejected_candidates", "occurred_at")
    op.drop_column("rejected_candidates", "app")
