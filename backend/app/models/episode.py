"""The structure layer: one episode is one sitting of dictation.

A sitting is a continuous stretch in one app, one thread, with one set of
people. Topics that recur across days are not episodes; they are entity
timelines. Boundaries are computed, never inferred by a model.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

EPISODE_STATUSES = ("open", "closed")
SUMMARY_STATUSES = ("verbatim", "generated", "skipped")

# Boundary constants. Changing either changes every episode in the corpus.
MAX_GAP_MINUTES = 30
MAX_EVENTS_PER_EPISODE = 25


class Episode(UserOwnedMixin, Base):
    __tablename__ = "episodes"

    # The sitting's identity: which conversation, in which app. Assignment
    # rebuilds all episodes sharing this key, so it is stored, not derived.
    group_key: Mapped[str] = mapped_column(Text, nullable=False)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    app: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)

    # Written on close, by the summariser. Null until then.
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    summary_status: Mapped[str | None] = mapped_column(Text)

    participant_entity_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(Uuid))
    topic_tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")

    __table_args__ = (
        Index("ix_episodes_user_started", "user_id", started_at.desc()),
        Index("ix_episodes_user_group", "user_id", "group_key", started_at.desc()),
        Index("ix_episodes_user_status", "user_id", "status"),
        Index("ix_episodes_participants", "participant_entity_ids", postgresql_using="gin"),
        Index("ix_episodes_topics", "topic_tags", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return (
            f"<Episode {self.group_key} {self.started_at:%Y-%m-%d %H:%M} "
            f"n={self.event_count} {self.status}>"
        )


class EpisodeEvent(Base):
    """Which events make up a sitting, in order.

    No surrogate key: the pair is the identity. `event_id` is unique on its
    own because an event belongs to exactly one episode -- an event reachable
    from no episode is unsearchable, and one reachable from two breaks
    provenance. The database enforces that rather than trusting the code.
    """

    __tablename__ = "episode_events"

    episode_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("episodes.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("events.id", ondelete="CASCADE"),
        primary_key=True,
        unique=True,
    )

    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    __table_args__ = (Index("ix_episode_events_episode_seq", "episode_id", "seq"),)

    def __repr__(self) -> str:
        return f"<EpisodeEvent {self.episode_id} #{self.seq}>"
