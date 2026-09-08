"""The evidence layer: one row per dictation, immutable after write."""

import uuid
from datetime import datetime

from sqlalchemy import REAL, DateTime, ForeignKey, Index, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

INGEST_STATUSES = ("pending", "processed", "ignored", "failed")

# Why an event was stored but not embedded. Every one of these is decided
# from the dictation itself -- no app knowledge, no model call.
IGNORE_REASONS = (
    "discarded_by_user",
    "low_asr_confidence",
    "implausible_timing",
    "no_content",
    "superseded_by_retry",
)


class Event(UserOwnedMixin, Base):
    __tablename__ = "events"

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Normalised on the way in, so nothing downstream has to re-derive it.
    app: Mapped[str | None] = mapped_column(Text)

    # Hash of the focused window title. Compared for equality and never
    # parsed: the title format differs per app, and a messenger's title is
    # usually a contact name, which we deliberately do not store.
    context_hash: Mapped[str | None] = mapped_column(Text)

    # Nullable so an ignored event can be recorded without storing its
    # content. The API schema requires both, so a real event always has them.
    raw_asr: Mapped[str | None] = mapped_column(Text)
    formatted_text: Mapped[str | None] = mapped_column(Text)

    # What the user kept after editing. The gap against formatted_text is the
    # only direct evidence of their own voice, and the best text to extract
    # from when it exists.
    committed_text: Mapped[str | None] = mapped_column(Text)

    asr_confidence: Mapped[float | None] = mapped_column(REAL)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    # Which episode this event belongs to. One column rather than a join
    # table: an event belongs to exactly one episode, and assignment needs to
    # find the events that belong to none yet, which a range query cannot
    # answer cheaply. Deleting an episode detaches its events rather than
    # destroying them -- the evidence layer outlives anything derived from it.
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="SET NULL")
    )

    # Set once an episode has extracted from this event. Until then the event
    # answers questions directly; afterwards the memory does.
    consolidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    ingest_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending"
    )
    ignore_reason: Mapped[str | None] = mapped_column(Text)

    source_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    external_id: Mapped[str | None] = mapped_column(Text)

    @property
    def canonical_text(self) -> str | None:
        """The text to extract from: what the user kept, else our best output."""
        return self.committed_text or self.formatted_text or self.raw_asr

    __table_args__ = (
        Index("ix_events_user_occurred", "user_id", occurred_at.desc()),
        Index("ix_events_user_app_occurred", "user_id", "app", occurred_at.desc()),
        Index("ix_events_user_app_context", "user_id", "app", "context_hash"),
        Index("ix_events_episode_occurred", "episode_id", occurred_at),
        # Partial: assignment only ever asks which events have no episode yet.
        Index(
            "ix_events_unassigned",
            "user_id",
            occurred_at,
            postgresql_where=episode_id.is_(None),
        ),
        # Partial: the fast path only ever looks for events not yet folded
        # into a memory, so the index stays small as the corpus grows.
        Index(
            "ix_events_unconsolidated",
            "user_id",
            occurred_at.desc(),
            postgresql_where=consolidated_at.is_(None),
        ),
        Index("ix_events_user_external_id", "user_id", "external_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Event {self.id} {self.app} {self.occurred_at:%Y-%m-%d %H:%M}>"
