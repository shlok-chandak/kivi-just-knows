"""Record why an episode closed, so a deliberate close can stay closed

Revision ID: bef3e688b1f3
Revises: 67c9cc9e0429
Create Date: 2026-09-12 09:09:50.002307

"""
from alembic import op
import sqlalchemy as sa


revision = 'bef3e688b1f3'
down_revision = '67c9cc9e0429'
branch_labels = None
depends_on = None


def upgrade():
    # Nullable and not backfilled. Episodes closed before this existed were
    # all closed by a time rule, and null already reads as "not by hand",
    # which is the only thing assignment asks.
    op.add_column('episodes', sa.Column('closed_by', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('episodes', 'closed_by')
