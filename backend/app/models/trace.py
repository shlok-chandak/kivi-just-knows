"""Traces: what the system did, and why, in plain language.

Not logging. A trace is a first-class record that every derived object can be
explained from, and every model call accounted for.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    REAL,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

TRACE_KINDS = ("ingest", "query")
TRACE_OUTCOMES = ("answered", "abstained", "partial", "error")

INGEST_STAGES = (
    "junk_gate",
    "embed",
    "episode_assign",
    "episode_consolidate",
    "ignore_filter",
    "entity_resolve",
    "claim_identity",
    "supersede",
    "belief_update",
)
QUERY_STAGES = (
    "parse",
    "plan",
    "sql_filter",
    "vector_search",
    "rank",
    "sufficiency_gate",
    "evidence_expand",
    "context_build",
    "generate",
    "verify",
)


class Trace(UserOwnedMixin, Base):
    __tablename__ = "traces"

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    input: Mapped[str | None] = mapped_column(Text)
    final_output: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)

    # What this trace was about: an episode id for ingest, null for a query.
    subject_key: Mapped[str | None] = mapped_column(Text)

    total_latency_ms: Mapped[int | None] = mapped_column(Integer)
    total_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    total_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    total_cost_usd: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)

    __table_args__ = (
        Index("ix_traces_user_created", "user_id", "kind"),
        Index("ix_traces_subject", "user_id", "subject_key"),
    )

    def __repr__(self) -> str:
        return f"<Trace {self.kind} {self.outcome} ${self.total_cost_usd:.6f}>"


class TraceStep(Base):
    """One stage of one trace.

    `decision` and `rationale` are written for a person to read -- "kept 4 of
    31 candidates: the time filter removed 27" -- because this is what the
    reasoning view renders.
    """

    __tablename__ = "trace_steps"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("traces.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    stage: Mapped[str] = mapped_column(Text, nullable=False)
    input_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decision: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)

    latency_ms: Mapped[int | None] = mapped_column(Integer)

    # Null on stages that are pure SQL or code. Set on every model call.
    model: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(REAL)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_trace_steps_trace_seq", "trace_id", "seq"),)

    def __repr__(self) -> str:
        return f"<TraceStep {self.seq} {self.stage} {self.decision}>"
