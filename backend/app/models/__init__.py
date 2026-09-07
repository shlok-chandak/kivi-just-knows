"""Model registry. Every model must be imported here for Alembic to see it."""

from app.models.base import Base, UserOwnedMixin
from app.models.episode import (
    EPISODE_STATUSES,
    MAX_EVENTS_PER_EPISODE,
    MAX_GAP_MINUTES,
    SUMMARY_STATUSES,
    Episode,
    EpisodeEvent,
)
from app.models.event import INGEST_STATUSES, Event
from app.models.job import JOB_STATUSES, SUBJECT_TYPES, Job
from app.models.trace import (
    INGEST_STAGES,
    QUERY_STAGES,
    TRACE_KINDS,
    TRACE_OUTCOMES,
    Trace,
    TraceStep,
)

__all__ = [
    "Base",
    "UserOwnedMixin",
    "Event",
    "INGEST_STATUSES",
    "Episode",
    "EpisodeEvent",
    "EPISODE_STATUSES",
    "SUMMARY_STATUSES",
    "MAX_GAP_MINUTES",
    "MAX_EVENTS_PER_EPISODE",
    "Job",
    "JOB_STATUSES",
    "SUBJECT_TYPES",
    "Trace",
    "TraceStep",
    "TRACE_KINDS",
    "TRACE_OUTCOMES",
    "INGEST_STAGES",
    "QUERY_STAGES",
]
