"""Work queue, held in Postgres rather than a broker.

Claimed with SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers take
different rows without blocking each other.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

JOB_STATUSES = ("pending", "running", "done", "failed")

# What a job acts on. Episode assignment rebuilds a whole group, so the
# subject is a group key; later stages act on a single episode.
SUBJECT_TYPES = ("group", "episode", "event")

DEFAULT_MAX_ATTEMPTS = 3


class Job(UserOwnedMixin, Base):
    __tablename__ = "jobs"

    stage: Mapped[str] = mapped_column(Text, nullable=False)

    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_key: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_MAX_ATTEMPTS
    )

    # Claimable only once now >= run_after. Backoff pushes this forward.
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        # Coalesce duplicate work, but only while a job is still waiting: a
        # job already running must not block a rebuild for a newer event.
        Index(
            "uq_jobs_pending_subject",
            "stage",
            "subject_key",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Job {self.stage} {self.subject_key} {self.status}>"
