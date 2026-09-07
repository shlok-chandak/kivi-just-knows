"""The evidence layer: one row per dictation, immutable after write."""

import uuid
from datetime import datetime

from sqlalchemy import REAL, DateTime, Index, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

INGEST_STATUSES = ("pending", "processed", "ignored", "failed")


class Event(UserOwnedMixin, Base):
    __tablename__ = "events"

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    app: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)

    # Reserved; populated only if a corpus supplies it. Not captured, never
    # inferred, and not used for grouping or filtering.
    recipients: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # Nullable so an ignored event can be recorded without storing its
    # content. The API schema requires both, so a real event always has them.
    raw_asr: Mapped[str | None] = mapped_column(Text)
    formatted_text: Mapped[str | None] = mapped_column(Text)

    # What the user kept after editing. Reserved for style learning.
    committed_text: Mapped[str | None] = mapped_column(Text)

    asr_confidence: Mapped[float | None] = mapped_column(REAL)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    ingest_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending"
    )
    ignore_reason: Mapped[str | None] = mapped_column(Text)

    source_batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    external_id: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_events_user_occurred", "user_id", occurred_at.desc()),
        Index("ix_events_user_app_occurred", "user_id", "app", occurred_at.desc()),
        Index("ix_events_user_thread", "user_id", "thread_id"),
        Index("ix_events_user_external_id", "user_id", "external_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Event {self.id} {self.app} {self.occurred_at:%Y-%m-%d %H:%M}>"
