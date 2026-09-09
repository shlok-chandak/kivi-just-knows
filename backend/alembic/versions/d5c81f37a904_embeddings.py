"""embeddings: one table, one HNSW index, dimension from the local model

Revision ID: d5c81f37a904
Revises: b2e6f8104c37
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "d5c81f37a904"
down_revision = "b2e6f8104c37"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 384


def upgrade() -> None:
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("object_type", sa.Text(), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("vector", Vector(EMBEDDING_DIM), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
    )
    op.create_index("ix_embeddings_user_id", "embeddings", ["user_id"])
    op.create_index("ix_embeddings_user_type", "embeddings", ["user_id", "object_type"])
    # Re-embedding must replace, not accumulate: duplicates would all match
    # the same query and crowd out everything else.
    op.create_index(
        "uq_embeddings_object_model",
        "embeddings",
        ["object_type", "object_id", "model"],
        unique=True,
    )
    # Cosine, because the sentence-transformers models are trained for it.
    op.execute(
        "CREATE INDEX ix_embeddings_vector ON embeddings "
        "USING hnsw (vector vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("embeddings")
