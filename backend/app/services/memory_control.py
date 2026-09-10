"""Seeing and deleting what is remembered.

The trust surface. Everything else in the system argues that the memory is
worth having; this is where the person gets to disagree and have it stick.

Deletion is real. The memory row goes, its evidence links go, and its vector
goes -- a vector left behind keeps the text findable through the excerpt
stored beside it, which would make "forget that" a lie told convincingly.

The dictations themselves stay. They are what the person actually said, not
what the system concluded, and deleting the record of speech because a
conclusion drawn from it was wrong destroys the evidence rather than the
belief. Forgetting an event is a separate request.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.embedding import Embedding
from app.models.memory import Memory, MemoryEvidence
from app.models.profile import ProfileEntry
from app.services import retrieval

logger = logging.getLogger("kivi.memory_control")

# How many matches a deletion may touch in one request. A forget that
# silently removed two hundred beliefs would be indistinguishable from a bug,
# so past this the person is told the count and asked to narrow it.
MAX_DELETIONS = 25

# Deletion needs a closer match than a search does. Returning a loosely
# related memory costs a scroll; deleting one costs the memory.
DELETE_SIMILARITY = 0.45


@dataclass
class MemoryView:
    """What is known, or what was removed."""

    topic: str | None
    memories: list[Memory] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    refused: str | None = None

    def as_dict(self) -> dict:
        return {
            "topic": self.topic,
            "memories": [
                {
                    "id": str(memory.id),
                    "type": memory.type,
                    "content": memory.content,
                    "status": memory.status,
                    "confidence": round(float(memory.posterior_mean or 0.0), 3),
                    "times_said": memory.observation_count,
                }
                for memory in self.memories
            ],
            "deleted": self.deleted,
            "refused": self.refused,
        }


def show(
    session: Session,
    user_id: uuid.UUID,
    *,
    topic: str = "",
    now: datetime | None = None,
    limit: int = 50,
) -> MemoryView:
    """What is remembered, everything or about one subject."""
    now = now or datetime.now(timezone.utc)

    if not topic.strip():
        memories = list(
            session.scalars(
                select(Memory)
                .where(Memory.user_id == user_id, Memory.status == "active")
                .order_by(Memory.type, Memory.last_reinforced_at.desc())
                .limit(limit)
            )
        )
        return MemoryView(topic=None, memories=memories)

    hits = retrieval.search_memories(session, user_id, topic, now=now, limit=limit)
    memories = [
        memory
        for memory in (session.get(Memory, hit.id) for hit in hits)
        if memory is not None
    ]
    return MemoryView(topic=topic, memories=memories)


def forget(
    session: Session,
    user_id: uuid.UUID,
    *,
    topic: str,
    now: datetime | None = None,
) -> MemoryView:
    """Delete what matches, for real.

    Refuses rather than guessing at two extremes: an empty topic, which would
    mean everything, and a topic matching more than a person could have
    meant. Both are better answered by asking than by acting.
    """
    now = now or datetime.now(timezone.utc)

    if not topic.strip():
        return MemoryView(
            topic=None,
            refused=(
                "Say what to forget. Deleting everything is a separate, "
                "explicit request."
            ),
        )

    hits = [
        hit
        for hit in retrieval.search_memories(
            session, user_id, topic, now=now, include_candidates=True, limit=100
        )
        if hit.similarity >= DELETE_SIMILARITY
    ]

    if not hits:
        return MemoryView(topic=topic, refused=f"Nothing remembered about {topic}.")

    if len(hits) > MAX_DELETIONS:
        return MemoryView(
            topic=topic,
            refused=(
                f"That matches {len(hits)} memories, which is more than this "
                f"is likely to mean. Narrow it and I will delete them."
            ),
        )

    ids = [hit.id for hit in hits]
    removed = [hit.text for hit in hits]

    _purge(session, user_id, ids)
    logger.info("forgot %d memories about %r", len(ids), topic[:60])

    return MemoryView(topic=topic, deleted=removed)


def _purge(session: Session, user_id: uuid.UUID, ids: Sequence[uuid.UUID]) -> None:
    """Remove the beliefs and everything that points at them.

    Order matters only for readability -- the foreign keys cascade -- but the
    vectors do not cascade, and one left behind keeps the text findable
    through the excerpt stored next to it.
    """
    if not ids:
        return

    session.execute(
        delete(MemoryEvidence).where(
            MemoryEvidence.user_id == user_id,
            MemoryEvidence.memory_id.in_(list(ids)),
        )
    )
    session.execute(
        delete(ProfileEntry).where(
            ProfileEntry.user_id == user_id,
            ProfileEntry.memory_id.in_(list(ids)),
        )
    )
    session.execute(
        delete(Embedding).where(
            Embedding.user_id == user_id,
            Embedding.object_type == "memory",
            Embedding.object_id.in_(list(ids)),
        )
    )
    session.execute(
        delete(Memory).where(Memory.user_id == user_id, Memory.id.in_(list(ids)))
    )
    session.flush()
