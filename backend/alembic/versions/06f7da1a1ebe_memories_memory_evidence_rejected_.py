"""memories, memory_evidence, rejected_candidates

Revision ID: 06f7da1a1ebe
Revises: 5a04e19b7742
Create Date: 2026-09-07 21:01:12.288067

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '06f7da1a1ebe'
down_revision = '5a04e19b7742'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('memories',
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('subject_entity_id', sa.Uuid(), nullable=True),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('attributes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('superseded_by', sa.Uuid(), nullable=True),
    sa.Column('alpha', sa.REAL(), nullable=False),
    sa.Column('beta', sa.REAL(), nullable=False),
    sa.Column('posterior_mean', sa.REAL(), sa.Computed('alpha / (alpha + beta)', persisted=True), nullable=False),
    sa.Column('observation_count', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.DateTime(timezone=True), nullable=True),
    sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_reinforced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['superseded_by'], ['memories.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memories_user_id'), 'memories', ['user_id'], unique=False)
    op.create_index('ix_memories_user_subject_status', 'memories', ['user_id', 'subject_entity_id', 'status'], unique=False)
    op.create_index('ix_memories_user_type_status', 'memories', ['user_id', 'type', 'status'], unique=False)
    op.create_table('memory_evidence',
    sa.Column('memory_id', sa.Uuid(), nullable=False),
    sa.Column('event_id', sa.Uuid(), nullable=False),
    sa.Column('episode_id', sa.Uuid(), nullable=True),
    sa.Column('stance', sa.Text(), nullable=False),
    sa.Column('weight', sa.REAL(), nullable=False),
    sa.Column('excerpt', sa.Text(), nullable=True),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['memory_id'], ['memories.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_memory_evidence_event', 'memory_evidence', ['event_id'], unique=False)
    op.create_index('ix_memory_evidence_memory', 'memory_evidence', ['memory_id'], unique=False)
    op.create_index(op.f('ix_memory_evidence_user_id'), 'memory_evidence', ['user_id'], unique=False)
    op.create_index('uq_memory_evidence_memory_event_stance', 'memory_evidence', ['memory_id', 'event_id', 'stance'], unique=True)
    op.create_table('rejected_candidates',
    sa.Column('event_id', sa.Uuid(), nullable=True),
    sa.Column('episode_id', sa.Uuid(), nullable=True),
    sa.Column('candidate', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('rejection_rule', sa.Text(), nullable=False),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['episode_id'], ['episodes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['event_id'], ['events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rejected_candidates_user_id'), 'rejected_candidates', ['user_id'], unique=False)
    op.create_index('ix_rejected_episode', 'rejected_candidates', ['episode_id'], unique=False)
    op.create_index('ix_rejected_user_at', 'rejected_candidates', ['user_id', 'at'], unique=False)
    op.create_index('ix_rejected_user_rule', 'rejected_candidates', ['user_id', 'rejection_rule'], unique=False)


def downgrade():
    op.drop_index('ix_rejected_user_rule', table_name='rejected_candidates')
    op.drop_index('ix_rejected_user_at', table_name='rejected_candidates')
    op.drop_index('ix_rejected_episode', table_name='rejected_candidates')
    op.drop_index(op.f('ix_rejected_candidates_user_id'), table_name='rejected_candidates')
    op.drop_table('rejected_candidates')
    op.drop_index('uq_memory_evidence_memory_event_stance', table_name='memory_evidence')
    op.drop_index(op.f('ix_memory_evidence_user_id'), table_name='memory_evidence')
    op.drop_index('ix_memory_evidence_memory', table_name='memory_evidence')
    op.drop_index('ix_memory_evidence_event', table_name='memory_evidence')
    op.drop_table('memory_evidence')
    op.drop_index('ix_memories_user_type_status', table_name='memories')
    op.drop_index('ix_memories_user_subject_status', table_name='memories')
    op.drop_index(op.f('ix_memories_user_id'), table_name='memories')
    op.drop_table('memories')
