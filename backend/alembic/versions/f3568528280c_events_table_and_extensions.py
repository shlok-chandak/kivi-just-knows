"""events table and extensions

Revision ID: f3568528280c
Revises: 
Create Date: 2026-09-07 13:24:24.526142

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'f3568528280c'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # pgvector backs semantic retrieval; pg_trgm backs fuzzy name matching.
    # Created up front so later migrations can declare columns that need them.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table('events',
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ingested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('app', sa.Text(), nullable=True),
    sa.Column('thread_id', sa.Text(), nullable=True),
    sa.Column('recipients', postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column('raw_asr', sa.Text(), nullable=True),
    sa.Column('formatted_text', sa.Text(), nullable=True),
    sa.Column('committed_text', sa.Text(), nullable=True),
    sa.Column('asr_confidence', sa.REAL(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('ingest_status', sa.Text(), nullable=False),
    sa.Column('ignore_reason', sa.Text(), nullable=True),
    sa.Column('source_batch_id', sa.Uuid(), nullable=True),
    sa.Column('external_id', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_events_recipients', 'events', ['recipients'], unique=False, postgresql_using='gin')
    op.create_index('ix_events_user_app_occurred', 'events', ['user_id', 'app', sa.text('occurred_at DESC')], unique=False)
    op.create_index('ix_events_user_external_id', 'events', ['user_id', 'external_id'], unique=True)
    op.create_index(op.f('ix_events_user_id'), 'events', ['user_id'], unique=False)
    op.create_index('ix_events_user_occurred', 'events', ['user_id', sa.text('occurred_at DESC')], unique=False)
    op.create_index('ix_events_user_thread', 'events', ['user_id', 'thread_id'], unique=False)


def downgrade():
    op.drop_index('ix_events_user_thread', table_name='events')
    op.drop_index('ix_events_user_occurred', table_name='events')
    op.drop_index(op.f('ix_events_user_id'), table_name='events')
    op.drop_index('ix_events_user_external_id', table_name='events')
    op.drop_index('ix_events_user_app_occurred', table_name='events')
    op.drop_index('ix_events_recipients', table_name='events', postgresql_using='gin')
    op.drop_table('events')
    # Extensions are left in place: other databases may share them, and
    # dropping them would cascade into any dependent object.
