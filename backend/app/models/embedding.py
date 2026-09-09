"""Vectors for the things that get searched.

One table for every kind of object rather than a vector column on each,
because retrieval asks the same question of all of them and the model that
produced a vector has to be recorded next to it. Mixing vectors from two
models in one index gives distances that mean nothing.

Dimension follows the model, and the model is local. That is what makes
embedding free enough to run on every event at ingest: no provider, no rate
limit, and no reason to batch for cost.
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Index, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

# What can carry a vector. Closed, so a typo cannot create a silent third
# category that nothing ever searches.
OBJECT_TYPES = ("event", "episode", "memory")

# all-MiniLM-L6-v2. Changing the model changes this, and every stored vector
# has to be rebuilt -- which is why `model` is on the row.
EMBEDDING_DIM = 384


class Embedding(UserOwnedMixin, Base):
    __tablename__ = "embeddings"

    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    # Which model produced this vector. Two models' vectors are not
    # comparable, so a query must filter on it rather than assume.
    model: Mapped[str] = mapped_column(Text, nullable=False)

    vector: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)

    # What was actually embedded. Not the whole text -- a prefix, so a wrong
    # result can be explained without joining back through three tables.
    excerpt: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # One vector per object per model. Re-embedding replaces rather than
        # accumulates, so a rerun cannot fill the index with duplicates that
        # all match the same query.
        Index(
            "uq_embeddings_object_model",
            "object_type",
            "object_id",
            "model",
            unique=True,
        ),
        Index(
            "ix_embeddings_vector",
            "vector",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"vector": "vector_cosine_ops"},
        ),
        Index("ix_embeddings_user_type", "user_id", "object_type"),
    )

    def __repr__(self) -> str:
        return f"<Embedding {self.object_type} {self.object_id}>"
