"""events: context_hash and consolidation, recipients dropped

Revision ID: a1c4e9b27d13
Revises: 06f7da1a1ebe
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "a1c4e9b27d13"
down_revision = "06f7da1a1ebe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # thread_id held an app-supplied conversation id; context_hash holds an
    # opaque hash of the window title. The values are not interchangeable, so
    # this is a drop and add rather than a rename.
    op.drop_index("ix_events_user_thread", table_name="events")
    op.drop_column("events", "thread_id")
    op.drop_column("events", "recipients")

    op.add_column("events", sa.Column("context_hash", sa.Text(), nullable=True))
    op.add_column(
        "events",
        sa.Column("consolidated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_events_user_app_context", "events", ["user_id", "app", "context_hash"]
    )
    # Partial: the fast path only reads events not yet folded into a memory.
    op.create_index(
        "ix_events_unconsolidated",
        "events",
        ["user_id", sa.text("occurred_at DESC")],
        postgresql_where=sa.text("consolidated_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_events_unconsolidated", table_name="events")
    op.drop_index("ix_events_user_app_context", table_name="events")
    op.drop_column("events", "consolidated_at")
    op.drop_column("events", "context_hash")

    op.add_column("events", sa.Column("recipients", ARRAY(sa.Text()), nullable=True))
    op.add_column("events", sa.Column("thread_id", sa.Text(), nullable=True))
    op.create_index("ix_events_user_thread", "events", ["user_id", "thread_id"])
