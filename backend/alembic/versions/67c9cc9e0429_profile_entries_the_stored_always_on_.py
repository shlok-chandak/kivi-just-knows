"""profile_entries: the stored always-on profile

Revision ID: 67c9cc9e0429
Revises: d5c81f37a904
Create Date: 2026-09-09 16:25:52.067518

"""
from alembic import op
import sqlalchemy as sa


revision = '67c9cc9e0429'
down_revision = 'd5c81f37a904'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profile_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "memory_id",
            sa.Uuid(),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="auto"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score", sa.Float(), nullable=True),
    )
    op.create_index("ix_profile_entries_user_id", "profile_entries", ["user_id"])
    # One row per memory: a memory cannot be pinned and hidden at once, nor
    # sit in both sections.
    op.create_index(
        "uq_profile_entry_memory",
        "profile_entries",
        ["user_id", "memory_id"],
        unique=True,
    )
    op.create_index(
        "ix_profile_entries_user_section",
        "profile_entries",
        ["user_id", "section", "position"],
    )


def downgrade():
    op.drop_index("ix_profile_entries_user_section", table_name="profile_entries")
    op.drop_index("uq_profile_entry_memory", table_name="profile_entries")
    op.drop_index("ix_profile_entries_user_id", table_name="profile_entries")
    op.drop_table("profile_entries")
