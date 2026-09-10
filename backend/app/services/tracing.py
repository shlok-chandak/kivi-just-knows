"""Recording what the pipeline did.

A trace is opened per unit of work, gains a step per stage, and closes with an
outcome. Model usage is written from the same object the LLM client returns,
so an accounted call is the easy path and a silent one takes effort.
"""

import time
import uuid
from types import TracebackType
from typing import Any, Self

from sqlalchemy.orm import Session

from app.llm.client import Usage
from app.models.trace import Trace, TraceStep


class TraceRecorder:
    """Collects steps for one trace and totals them on close.

    Usable as a context manager: leaving the block closes the trace, marking
    it 'error' if an exception is propagating.
    """

    def __init__(
        self,
        session: Session,
        *,
        user_id: uuid.UUID,
        kind: str,
        input_text: str | None = None,
        subject_key: str | None = None,
    ) -> None:
        self.session = session
        self.trace = Trace(
            user_id=user_id,
            kind=kind,
            input=input_text,
            subject_key=subject_key,
            total_input_tokens=0,
            total_output_tokens=0,
            total_cost_usd=0.0,
        )
        session.add(self.trace)
        session.flush()

        self._seq = 0
        self._started = time.perf_counter()

    @property
    def id(self) -> uuid.UUID:
        return self.trace.id

    def step(
        self,
        stage: str,
        *,
        decision: str,
        rationale: str | None = None,
        usage: Usage | None = None,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        latency_ms: int | None = None,
    ) -> TraceStep:
        """Record one stage.

        `decision` says what was chosen; `rationale` says why, in language a
        person can read. Pass `usage` whenever a model was called.
        """
        self._seq += 1

        record = TraceStep(
            trace_id=self.trace.id,
            seq=self._seq,
            stage=stage,
            decision=decision,
            rationale=rationale,
            input_summary=input_summary,
            output_summary=output_summary,
            latency_ms=usage.latency_ms if usage else latency_ms,
            model=usage.model if usage else None,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            cost_usd=usage.cost_usd if usage else None,
        )
        self.session.add(record)

        if usage:
            self.trace.total_input_tokens += usage.input_tokens
            self.trace.total_output_tokens += usage.output_tokens
            self.trace.total_cost_usd += usage.cost_usd

        self.session.flush()
        return record

    def close(self, outcome: str, final_output: str | None = None) -> Trace:
        self.trace.outcome = outcome
        self.trace.final_output = final_output
        self.trace.total_latency_ms = int((time.perf_counter() - self._started) * 1000)
        self.session.flush()
        return self.trace

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.trace.outcome is None:
            self.close("error" if exc_type else "answered")


def start_ingest_trace(
    session: Session, *, user_id: uuid.UUID, subject_key: str, input_text: str | None = None
) -> TraceRecorder:
    return TraceRecorder(
        session,
        user_id=user_id,
        kind="ingest",
        subject_key=subject_key,
        input_text=input_text,
    )


def start_query_trace(
    session: Session, *, user_id: uuid.UUID, request: str
) -> TraceRecorder:
    """Open a trace for one thing a person asked.

    The request text is the input, so a trace can be replayed later against
    whatever is known by then.
    """
    return TraceRecorder(
        session,
        user_id=user_id,
        kind="query",
        input_text=request,
    )
