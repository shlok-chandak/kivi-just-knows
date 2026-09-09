"""The only module that produces vectors.

Mirrors app/llm/client.py: one boundary, so swapping the model means editing
this file and nothing else. The difference is that this one runs locally.
That is not a detail -- it is what makes embedding free enough to run on
every event synchronously at ingest, which is what makes a dictation
searchable seconds after it is spoken rather than after an episode closes.

No provider, so no rate limit, no quota, no outage, and no reason to batch
for cost. Batching here is only about CPU efficiency.
"""

import logging
import uuid
from typing import Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.embedding import EMBEDDING_DIM, Embedding

logger = logging.getLogger("kivi.embed")

# What is stored alongside a vector so a surprising result can be explained.
EXCERPT_CHARS = 300

_model = None


def _get_model():
    """Load once per process. First call is slow, the rest are not."""
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        logger.info("loading embedding model %s", settings.embedding_model)
        _model = TextEmbedding(settings.embedding_model)
    return _model


def encode(texts: Sequence[str]) -> list[list[float]]:
    """Vectors for these texts, in order.

    Kept separate from anything that touches the database so it can be called
    from a query path, a test, or a prototype comparison without a session.
    """
    if not texts:
        return []
    vectors = [vector.tolist() for vector in _get_model().embed(list(texts))]
    if vectors and len(vectors[0]) != EMBEDDING_DIM:
        raise RuntimeError(
            f"{settings.embedding_model} produced {len(vectors[0])} dimensions, "
            f"but the embeddings column is {EMBEDDING_DIM}. Changing model "
            f"means a migration and a rebuild of every stored vector."
        )
    return vectors


def encode_one(text: str) -> list[float]:
    return encode([text])[0]


def store(
    session: Session,
    user_id: uuid.UUID,
    items: Iterable[tuple[str, uuid.UUID, str]],
) -> int:
    """Embed and save (object_type, object_id, text) triples.

    Replaces any existing vector for the same object and model rather than
    adding one. Duplicates would all match the same query and crowd out
    everything else, so a rerun has to be idempotent.
    """
    rows = [item for item in items if item[2] and item[2].strip()]
    if not rows:
        return 0

    vectors = encode([text for _, _, text in rows])

    session.execute(
        delete(Embedding).where(
            Embedding.user_id == user_id,
            Embedding.model == settings.embedding_model,
            Embedding.object_id.in_([object_id for _, object_id, _ in rows]),
        )
    )

    for (object_type, object_id, text), vector in zip(rows, vectors):
        session.add(
            Embedding(
                id=uuid.uuid4(),
                user_id=user_id,
                object_type=object_type,
                object_id=object_id,
                model=settings.embedding_model,
                vector=vector,
                excerpt=text[:EXCERPT_CHARS],
            )
        )

    session.flush()
    return len(rows)


def nearest(
    session: Session,
    user_id: uuid.UUID,
    object_type: str,
    vector: Sequence[float],
    *,
    ids: Sequence[uuid.UUID] | None = None,
    limit: int = 10,
) -> list[tuple[uuid.UUID, float]]:
    """The closest stored vectors to this one, nearest first.

    Deliberately smaller than a search: no filtering by time, no decay, no
    lexical arm, no ranking. Two callers want different things from a
    vector. Search asks "what should I show someone", and has to weigh
    freshness and confidence to answer it. Claim identity asks "have I been
    told this before", where freshness is irrelevant -- a belief stated
    three months ago is exactly the one a restatement should reinforce.

    Ranking the second question by the first question's rules is what makes
    a system quietly stop noticing that it already knows something.
    """
    distance = Embedding.vector.cosine_distance(list(vector))
    stmt = (
        select(Embedding.object_id, distance.label("distance"))
        .where(
            Embedding.user_id == user_id,
            Embedding.object_type == object_type,
            # A vector from another model is not comparable to this one.
            Embedding.model == settings.embedding_model,
        )
        .order_by(distance)
        .limit(limit)
    )
    if ids is not None:
        if not ids:
            return []
        stmt = stmt.where(Embedding.object_id.in_(list(ids)))

    return [
        (object_id, 1.0 - float(distance_value))
        for object_id, distance_value in session.execute(stmt).all()
    ]


def missing(
    session: Session, user_id: uuid.UUID, object_type: str, object_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """Which of these objects have no vector for the current model.

    Used to backfill after a model change, and to make re-embedding cheap
    enough to run without thinking about it.
    """
    if not object_ids:
        return []
    present = set(
        session.scalars(
            select(Embedding.object_id).where(
                Embedding.user_id == user_id,
                Embedding.object_type == object_type,
                Embedding.model == settings.embedding_model,
                Embedding.object_id.in_(object_ids),
            )
        )
    )
    return [object_id for object_id in object_ids if object_id not in present]
