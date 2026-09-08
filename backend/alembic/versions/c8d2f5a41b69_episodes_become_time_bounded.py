"""episodes become time-bounded and cross-app

Revision ID: c8d2f5a41b69
Revises: a1c4e9b27d13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "c8d2f5a41b69"
down_revision = "a1c4e9b27d13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Membership moves onto events. Assignment has to find events belonging to
    # no episode, which the old join table answered by absence and a range
    # query cannot answer cheaply at all.
    op.drop_table("episode_events")

    op.add_column(
        "events",
        sa.Column(
            "episode_id",
            sa.Uuid(),
            sa.ForeignKey("episodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_events_episode_occurred", "events", ["episode_id", "occurred_at"]
    )
    op.create_index(
        "ix_events_unassigned",
        "events",
        ["user_id", "occurred_at"],
        postgresql_where=sa.text("episode_id IS NULL"),
    )

    # An episode now spans apps and is identified by its time range, so the
    # columns describing one conversation no longer describe anything.
    op.drop_index("ix_episodes_user_group", table_name="episodes")
    op.drop_index("ix_episodes_user_status", table_name="episodes")
    op.drop_index("ix_episodes_participants", table_name="episodes")
    op.drop_column("episodes", "group_key")
    op.drop_column("episodes", "app")
    op.drop_column("episodes", "thread_id")
    op.drop_column("episodes", "participant_entity_ids")

    op.create_index(
        "ix_episodes_open",
        "episodes",
        ["user_id"],
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ix_episodes_open", table_name="episodes")

    op.add_column(
        "episodes", sa.Column("participant_entity_ids", ARRAY(sa.Uuid()), nullable=True)
    )
    op.add_column("episodes", sa.Column("thread_id", sa.Text(), nullable=True))
    op.add_column("episodes", sa.Column("app", sa.Text(), nullable=True))
    op.add_column(
        "episodes",
        sa.Column("group_key", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_episodes_participants",
        "episodes",
        ["participant_entity_ids"],
        postgresql_using="gin",
    )
    op.create_index("ix_episodes_user_status", "episodes", ["user_id", "status"])
    op.create_index(
        "ix_episodes_user_group",
        "episodes",
        ["user_id", "group_key", sa.text("started_at DESC")],
    )

    op.drop_index("ix_events_unassigned", table_name="events")
    op.drop_index("ix_events_episode_occurred", table_name="events")
    op.drop_column("events", "episode_id")

    op.create_table(
        "episode_events",
        sa.Column(
            "episode_id",
            sa.Uuid(),
            sa.ForeignKey("episodes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "event_id",
            sa.Uuid(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
            unique=True,
        ),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
    )
    op.create_index(
        "ix_episode_events_episode_seq", "episode_events", ["episode_id", "seq"]
    )
