"""Reading back what the system did.

A trace is the answer to "why did it say that". Every stage records what it
decided and why, along with what the model calls cost, so a wrong answer is
diagnosable rather than merely disappointing.

Replay re-asks a stored question, optionally with something taken away. Plain
replay shows whether the answer has changed as the system learned more.
Replay with beliefs excluded answers the sharper question -- did this memory
actually affect the result, or would the answer have been the same without
it. Claiming memory mattered is easy; removing it and showing the answer
change is the proof.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Header, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ask import reference_now
from app.config import settings
from app.db.session import get_db
from app.models.trace import Trace, TraceStep
from app.services import asking
from app.services.ablation import Ablation

router = APIRouter(prefix="/traces", tags=["traces"])


def _trace(trace: Trace) -> dict:
    return {
        "id": str(trace.id),
        "kind": trace.kind,
        "input": trace.input,
        "final_output": trace.final_output,
        "outcome": trace.outcome,
        "subject_key": trace.subject_key,
        "latency_ms": trace.total_latency_ms,
        "tokens": {
            "input": trace.total_input_tokens,
            "output": trace.total_output_tokens,
        },
        "cost_usd": round(float(trace.total_cost_usd or 0.0), 6),
        "at": trace.created_at.isoformat() if trace.created_at else None,
    }


def _step(step: TraceStep) -> dict:
    return {
        "seq": step.seq,
        "stage": step.stage,
        "decision": step.decision,
        "rationale": step.rationale,
        "input": step.input_summary,
        "output": step.output_summary,
        "latency_ms": step.latency_ms,
        "model": step.model,
        "tokens": (
            {"input": step.input_tokens, "output": step.output_tokens}
            if step.model
            else None
        ),
        "cost_usd": round(float(step.cost_usd), 6) if step.cost_usd else None,
    }


@router.get("")
def recent(
    db: Session = Depends(get_db),
    kind: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
) -> dict:
    """The most recent traces, newest first."""
    query = select(Trace).where(Trace.user_id == settings.default_user_id)
    if kind:
        query = query.where(Trace.kind == kind)
    if outcome:
        query = query.where(Trace.outcome == outcome)

    traces = list(
        db.scalars(query.order_by(Trace.created_at.desc()).limit(limit))
    )
    return {"traces": [_trace(trace) for trace in traces], "count": len(traces)}


@router.get("/{trace_id}")
def detail(trace_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """One trace, stage by stage."""
    trace = _load(db, trace_id)
    steps = list(
        db.scalars(
            select(TraceStep)
            .where(TraceStep.trace_id == trace.id)
            .order_by(TraceStep.seq)
        )
    )
    return {**_trace(trace), "steps": [_step(step) for step in steps]}


class ReplayRequest(BaseModel):
    """What to withhold from the re-run. Empty means a plain replay."""

    exclude_memory_ids: list[uuid.UUID] = Field(default_factory=list)
    skip_filters: bool = False
    disable_stages: list[str] = Field(default_factory=list)


@router.post("/{trace_id}/replay")
def replay(
    trace_id: uuid.UUID,
    payload: ReplayRequest | None = None,
    db: Session = Depends(get_db),
    x_kivi_now: str | None = Header(default=None),
) -> dict:
    """Ask the same question again, optionally with something taken away.

    Only queries can be replayed. Re-running an ingest would extract the same
    dictation a second time and write beliefs, which is a different and much
    less reversible thing than asking a question twice.

    Nothing is deleted. Exclusions apply to this run alone, so the question
    asked normally afterwards behaves exactly as it did before.
    """
    original = _load(db, trace_id)
    if original.kind != "query":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only query traces can be replayed.",
        )
    if not original.input:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This trace did not record the request text.",
        )

    cuts = Ablation.parse(payload.model_dump(mode="json") if payload else None)

    result = asking.run(
        db,
        original.user_id,
        original.input,
        now=reference_now(x_kivi_now),
        cuts=cuts,
    )
    db.commit()

    now_text = result.as_dict()
    answer = _final(now_text)

    return {
        "request": original.input,
        "withheld": cuts.as_dict() if cuts.active else None,
        "then": {
            "trace_id": str(original.id),
            "at": original.created_at.isoformat() if original.created_at else None,
            "outcome": original.outcome,
            "answer": original.final_output,
        },
        "now": {
            "trace_id": str(result.trace_id),
            "outcome": result.outcome,
            "answer": answer,
        },
        "changed": (original.final_output or "") != (answer or ""),
        "replay": now_text,
    }


def _final(payload: dict) -> str | None:
    for step in reversed(payload.get("steps", [])):
        result = step.get("result") or {}
        text = result.get("answer") or result.get("text")
        if text:
            return text
    return None


def _load(db: Session, trace_id: uuid.UUID) -> Trace:
    trace = db.get(Trace, trace_id)
    if trace is None or trace.user_id != settings.default_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such trace.")
    return trace
