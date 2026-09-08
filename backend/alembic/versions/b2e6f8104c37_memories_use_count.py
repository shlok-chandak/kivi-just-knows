"""memories: count how often a claim is actually used

Revision ID: b2e6f8104c37
Revises: f7a3d0c62e18
"""

import sqlalchemy as sa
from alembic import op

revision = "b2e6f8104c37"
down_revision = "f7a3d0c62e18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("memories", "use_count")
