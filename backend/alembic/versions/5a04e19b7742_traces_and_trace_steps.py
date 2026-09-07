"""traces and trace_steps

Revision ID: 5a04e19b7742
Revises: bf6e7ee69b39
Create Date: 2026-09-07 18:53:42.033766

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '5a04e19b7742'
down_revision = 'bf6e7ee69b39'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('traces',
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('input', sa.Text(), nullable=True),
    sa.Column('final_output', sa.Text(), nullable=True),
    sa.Column('outcome', sa.Text(), nullable=True),
    sa.Column('subject_key', sa.Text(), nullable=True),
    sa.Column('total_latency_ms', sa.Integer(), nullable=True),
    sa.Column('total_input_tokens', sa.Integer(), nullable=False),
    sa.Column('total_output_tokens', sa.Integer(), nullable=False),
    sa.Column('total_cost_usd', sa.REAL(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_traces_subject', 'traces', ['user_id', 'subject_key'], unique=False)
    op.create_index('ix_traces_user_created', 'traces', ['user_id', 'kind'], unique=False)
    op.create_index(op.f('ix_traces_user_id'), 'traces', ['user_id'], unique=False)
    op.create_table('trace_steps',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('trace_id', sa.Uuid(), nullable=False),
    sa.Column('seq', sa.SmallInteger(), nullable=False),
    sa.Column('stage', sa.Text(), nullable=False),
    sa.Column('input_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('output_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('decision', sa.Text(), nullable=True),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('model', sa.Text(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.REAL(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['trace_id'], ['traces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trace_steps_trace_seq', 'trace_steps', ['trace_id', 'seq'], unique=False)


def downgrade():
    op.drop_index('ix_trace_steps_trace_seq', table_name='trace_steps')
    op.drop_table('trace_steps')
    op.drop_index(op.f('ix_traces_user_id'), table_name='traces')
    op.drop_index('ix_traces_user_created', table_name='traces')
    op.drop_index('ix_traces_subject', table_name='traces')
    op.drop_table('traces')
