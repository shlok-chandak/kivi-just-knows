"""The structure layer: one episode is one stretch of activity.

An episode spans whatever the user was doing in that stretch, across every
app, and closes on idleness or at a maximum span. Boundaries are time, never
a model's judgement, and never semantic similarity -- similarity would make
assignment depend on ingest order and destroy the time filters retrieval
needs.

Closing is final. An episode is assembled once and never re-partitioned,
which is what keeps its summary from ever describing a different set of
events than the one it holds.
"""

from datetime import datetime, timedelta

from sqlalchemy import DateTime, Index, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

EPISODE_STATUSES = ("open", "closed")

# 'withheld' means the episode was sensitive and the user has not opted into
# remembering such content: the events remain, the summary is deliberately
# absent so nothing derived becomes searchable.
SUMMARY_STATUSES = ("verbatim", "generated", "skipped", "withheld")

# Boundary constants. Changing either changes every episode in the corpus.
IDLE_MINUTES = 20
MAX_SPAN_HOURS = 2
MAX_EVENTS_PER_EPISODE = 60

IDLE_GAP = timedelta(minutes=IDLE_MINUTES)
MAX_SPAN = timedelta(hours=MAX_SPAN_HOURS)

# Sitting boundaries inside an episode. Not persisted -- these only shape the
# prompt, so the model can see which lines belonged together. The gap depends
# on how much evidence there is for grouping: a shared context holds a
# conversation together, whereas app alone is weak and needs proximity.
SITTING_GAP_WITH_CONTEXT = timedelta(minutes=30)
SITTING_GAP_APP_ONLY = timedelta(minutes=5)


class Episode(UserOwnedMixin, Base):
    __tablename__ = "episodes"

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Written on close, by consolidation. Null until then.
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_status: Mapped[str | None] = mapped_column(Text)
    topic_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")

    __table_args__ = (
        Index("ix_episodes_user_started", "user_id", started_at.desc()),
        # Partial: the closer only ever looks for episodes still open, and
        # there is at most one of those per user.
        Index(
            "ix_episodes_open",
            "user_id",
            postgresql_where=status == "open",
        ),
        Index("ix_episodes_topics", "topic_tags", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return (
            f"<Episode {self.started_at:%Y-%m-%d %H:%M} "
            f"n={self.event_count} {self.status}>"
        )
