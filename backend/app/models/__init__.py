"""Model registry. Every model must be imported here for Alembic to see it."""

from app.models.base import Base, UserOwnedMixin
from app.models.embedding import (
    EMBEDDING_DIM,
    OBJECT_TYPES,
    Embedding,
)
from app.models.episode import (
    EPISODE_STATUSES,
    IDLE_MINUTES,
    MAX_EVENTS_PER_EPISODE,
    MAX_SPAN_HOURS,
    SUMMARY_STATUSES,
    Episode,
)
from app.models.event import IGNORE_REASONS, INGEST_STATUSES, Event
from app.models.job import JOB_STATUSES, SUBJECT_TYPES, Job
from app.models.memory import (
    MEMORY_STATUSES,
    MEMORY_TYPES,
    STANCES,
    Memory,
    MemoryEvidence,
)
from app.models.rejected import REJECTION_RULES, RejectedCandidate
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
    "Embedding",
    "EMBEDDING_DIM",
    "OBJECT_TYPES",
    "IGNORE_REASONS",
    "Episode",
    "EPISODE_STATUSES",
    "SUMMARY_STATUSES",
    "IDLE_MINUTES",
    "MAX_SPAN_HOURS",
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
    "Memory",
    "MemoryEvidence",
    "MEMORY_TYPES",
    "MEMORY_STATUSES",
    "STANCES",
    "RejectedCandidate",
    "REJECTION_RULES",
]
