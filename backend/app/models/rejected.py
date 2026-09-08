"""What was heard and deliberately not kept.

Ignoring something silently cannot be shown to anyone, so every decision not
to remember writes a row here with the rule that made it. The same table is
the cost-control record: candidates skipped before reaching a model are
logged rather than merely absent.

A refusal at ingest has no event to point at, because the point of refusing
is that nothing was stored. Those rows carry their own `app` and
`occurred_at` instead, so the user can see that something was declined at a
particular time in a particular place -- a visible gap rather than a silent
one -- without the content being kept to show them.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

REJECTION_RULES = (
    # Refused at ingest: nothing was stored, so there is no event to inspect.
    "sensitive_category",
    # Found by the model while reading an episode, and purged afterwards.
    "sensitive_on_review",
    "transient",
    "not_about_user",
    "single_observation",
    "duplicate",
    "content_correction",
    "low_confidence",
    "no_extractable_content",
)


class RejectedCandidate(UserOwnedMixin, Base):
    __tablename__ = "rejected_candidates"

    # Nullable: an episode can be rejected before any single event is blamed
    # for it, and a dictation refused at ingest has no event at all.
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("events.id", ondelete="CASCADE")
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="SET NULL")
    )

    # Where and when, for refusals that have no event to join to. Never the
    # content: for a sensitive refusal the content is the thing being refused.
    app: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The candidate as proposed, so the user can see what was discarded. Left
    # null whenever showing it would defeat the refusal.
    candidate: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    rejection_rule: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)

    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_rejected_user_rule", "user_id", "rejection_rule"),
        Index("ix_rejected_user_at", "user_id", "at"),
        Index("ix_rejected_episode", "episode_id"),
    )

    def __repr__(self) -> str:
        return f"<RejectedCandidate {self.rejection_rule}>"
