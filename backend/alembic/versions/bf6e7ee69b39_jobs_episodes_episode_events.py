"""jobs, episodes, episode_events

Revision ID: bf6e7ee69b39
Revises: f3568528280c
Create Date: 2026-09-07 17:25:59.152616

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'bf6e7ee69b39'
down_revision = 'f3568528280c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('episodes',
    sa.Column('group_key', sa.Text(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('app', sa.Text(), nullable=True),
    sa.Column('thread_id', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('summary_status', sa.Text(), nullable=True),
    sa.Column('participant_entity_ids', postgresql.ARRAY(sa.Uuid()), nullable=True),
    sa.Column('topic_tags', postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column('event_count', sa.Integer(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_episodes_participants', 'episodes', ['participant_entity_ids'], unique=False, postgresql_using='gin')
    op.create_index('ix_episodes_topics', 'episodes', ['topic_tags'], unique=False, postgresql_using='gin')
    op.create_index('ix_episodes_user_group', 'episodes', ['user_id', 'group_key', sa.text('started_at DESC')], unique=False)
    op.create_index(op.f('ix_episodes_user_id'), 'episodes', ['user_id'], unique=False)
    op.create_index('ix_episodes_user_started', 'episodes', ['user_id', sa.text('started_at DESC')], unique=False)
    op.create_index('ix_episodes_user_status', 'episodes', ['user_id', 'status'], unique=False)
    op.create_table('jobs',
    sa.Column('stage', sa.Text(), nullable=False),
    sa.Column('subject_type', sa.Text(), nullable=False),
    sa.Column('subject_key', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_jobs_claim', 'jobs', ['status', 'run_after'], unique=False)
    op.create_index(op.f('ix_jobs_user_id'), 'jobs', ['user_id'], unique=False)
    op.create_index('uq_jobs_pending_subject', 'jobs', ['stage', 'subject_key'], unique=True, postgresql_where=sa.text("status = 'pending'"))
    op.create_table('episode_events',
    sa.Column('episode_id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.SmallInteger(), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('episode_id', 'event_id'),
    sa.UniqueConstraint('event_id')
    )
    op.create_index('ix_episode_events_episode_seq', 'episode_events', ['episode_id', 'seq'], unique=False)


def downgrade():
    op.drop_index('ix_episode_events_episode_seq', table_name='episode_events')
    op.drop_table('episode_events')
    op.drop_index('uq_jobs_pending_subject', table_name='jobs', postgresql_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_jobs_user_id'), table_name='jobs')
    op.drop_index('ix_jobs_claim', table_name='jobs')
    op.drop_table('jobs')
    op.drop_index('ix_episodes_user_status', table_name='episodes')
    op.drop_index('ix_episodes_user_started', table_name='episodes')
    op.drop_index(op.f('ix_episodes_user_id'), table_name='episodes')
    op.drop_index('ix_episodes_user_group', table_name='episodes')
    op.drop_index('ix_episodes_topics', table_name='episodes', postgresql_using='gin')
    op.drop_index('ix_episodes_participants', table_name='episodes', postgresql_using='gin')
    op.drop_table('episodes')
