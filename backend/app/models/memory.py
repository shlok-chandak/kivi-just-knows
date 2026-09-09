"""The understanding layer: individual claims, and what justifies them.

A memory is one claim in one sentence, carrying a belief about how likely it
is to be true and rows pointing at the dictations it came from. A memory
without evidence is a bug, not a weak memory.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

MEMORY_TYPES = ("fact", "decision", "commitment", "preference")

# candidate  -> seen, but not trusted enough to state as fact
# active     -> current and citable
# superseded -> replaced by a newer claim, still queryable for history
#
# Three, not five. "expired" was derivable from valid_until and did not need
# storing; "invalidated" overlapped superseded with no rule to tell them
# apart, which left a state nothing could reach deliberately.
MEMORY_STATUSES = ("candidate", "active", "superseded")

STANCES = ("supports", "contradicts")

# Uninformative prior: no evidence either way.
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0

# How much one observation is worth. Confidence accumulates from these, so a
# corroborated claim scores above a one-off remark.
WEIGHT_EXPLICIT = 1.0
WEIGHT_INFERRED = 0.4

# Weight of the user reverting a preference the system applied. Higher than a
# plain contradiction: an undo is a deliberate correction.
WEIGHT_REVERTED = 1.5

# Supporting weight for a preference that was applied and left alone. Without
# it, evidence dries up once the system stops making the mistake and naive
# counting decays a correct belief to nothing.
WEIGHT_APPLIED_UNREVERTED = 0.2

# How fast a claim of each type stops being worth volunteering. Applied at
# read time against last_reinforced_at -- never written back, so the evidence
# count stays an honest record of what the user actually said.

HALF_LIFE_DAYS = {
    "preference": 365,
    "decision": 365,
    "fact": 90,
    "commitment": 30,
}


class Memory(UserOwnedMixin, Base):
    __tablename__ = "memories"

    type: Mapped[str] = mapped_column(Text, nullable=False)

    # The entity this claim is about. Plain column rather than a foreign key:
    # entity resolution does not exist yet, and a key to a missing table would
    # block ingestion. Becomes a real reference once entities land.
    subject_entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)

    # What is being claimed. One claim, one sentence.
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # The identity of the claim itself: what makes two statements the same
    # statement, so restating reinforces and changing the value supersedes.
    # Currently a normalised hash of subject and content; becomes resolved
    # entity plus canonical predicate once those exist.
    claim_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Structured extras that do not deserve columns: the unresolved subject
    # name until entity resolution runs, and the operator/context key for
    # style preferences.
    attributes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="SET NULL")
    )

    # Beta belief, accumulated from evidence: each supporting observation adds
    # its weight to alpha, each contradicting one to beta. Confidence is
    # therefore a function of how well corroborated a claim is, not a label.
    # Whether a claim is citable is `status`, which is a separate decision.
    alpha: Mapped[float] = mapped_column(REAL, nullable=False, default=PRIOR_ALPHA)
    beta: Mapped[float] = mapped_column(REAL, nullable=False, default=PRIOR_BETA)
    posterior_mean: Mapped[float] = mapped_column(
        REAL, Computed("alpha / (alpha + beta)", persisted=True)
    )

    observation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # How often this claim has actually been used to answer something. A
    # belief the user relies on weekly is worth more than one nobody has
    # needed, and neither the evidence count nor the timestamp shows that.
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Drives currency at read time: how long ago the user last said this.
    last_reinforced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # Commitments stop being worth volunteering once they are due. Facts and
    # preferences decay instead, which is ranking rather than an end date.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_memories_user_type_status", "user_id", "type", "status"),
        Index("ix_memories_user_subject_status", "user_id", "subject_entity_id", "status"),
        # At most one live memory per claim. Two contradictory beliefs held at
        # once are not discouraged here, they are impossible to insert -- a
        # constraint has no code path that forgets to check.
        Index(
            "uq_memories_live_claim",
            "user_id",
            "claim_key",
            unique=True,
            postgresql_where=text("status <> 'superseded'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Memory {self.type} {self.status} {self.content[:40]!r}>"


class MemoryEvidence(UserOwnedMixin, Base):
    """A dictation that supports or contradicts a claim.

    `excerpt` is a span of the event's own formatted text, so provenance can
    show the user the words that produced the claim rather than a paraphrase.
    """

    __tablename__ = "memory_evidence"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="SET NULL")
    )

    stance: Mapped[str] = mapped_column(Text, nullable=False, default="supports")
    weight: Mapped[float] = mapped_column(REAL, nullable=False, default=WEIGHT_INFERRED)
    excerpt: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_memory_evidence_memory", "memory_id"),
        Index("ix_memory_evidence_event", "event_id"),
        # One event supports a given claim once. Re-running extraction must
        # not inflate a belief by recording the same observation twice.
        Index(
            "uq_memory_evidence_memory_event_stance",
            "memory_id",
            "event_id",
            "stance",
            unique=True,
        ),
    )

    def __repr__(self) -> str:
        return f"<MemoryEvidence {self.stance} w={self.weight}>"
