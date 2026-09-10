"""Where a belief came from, and why it is held as strongly as it is.

Two questions a person will ask of anything the system claims to know. The
first is answered from the evidence rows, which point at the dictations the
claim was drawn from; the second from the belief itself, taken apart into the
numbers that produced it.

Neither computes anything new. Showing a confidence derived differently from
the one used to rank would be a nicer explanation of the wrong system.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.ask import reference_now
from app.config import settings
from app.db.session import get_db
from app.models.event import Event
from app.models.memory import Memory, MemoryEvidence
from app.models.trace import Trace
from app.services import ranking

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("/{memory_id}/provenance")
def provenance(memory_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """The dictations this claim was drawn from, oldest first."""
    memory = _load(db, memory_id)

    rows = list(
        db.execute(
            select(MemoryEvidence, Event)
            .join(Event, Event.id == MemoryEvidence.event_id)
            .where(MemoryEvidence.memory_id == memory.id)
            .order_by(MemoryEvidence.observed_at)
        )
    )

    episode_ids = {
        str(evidence.episode_id) for evidence, _ in rows if evidence.episode_id
    }
    traces = (
        list(
            db.scalars(
                select(Trace).where(
                    Trace.user_id == memory.user_id,
                    Trace.subject_key.in_(episode_ids),
                )
            )
        )
        if episode_ids
        else []
    )

    return {
        "memory": {
            "id": str(memory.id),
            "type": memory.type,
            "content": memory.content,
            "status": memory.status,
        },
        "evidence": [
            {
                "event_id": str(event.id),
                "occurred_at": event.occurred_at.isoformat(),
                "app": event.app,
                "stance": evidence.stance,
                "weight": round(float(evidence.weight), 3),
                # The user's own words, not a paraphrase.
                "excerpt": evidence.excerpt,
                "said": event.canonical_text,
                "edited": bool(
                    event.committed_text
                    and event.committed_text != event.formatted_text
                ),
            }
            for evidence, event in rows
        ],
        # The ingest runs that produced this claim, for the full reasoning.
        "traces": [str(trace.id) for trace in traces],
        "count": len(rows),
    }


@router.get("/{memory_id}/belief")
def belief(
    memory_id: uuid.UUID,
    db: Session = Depends(get_db),
    x_kivi_now: str | None = Header(default=None),
) -> dict:
    """The two numbers behind this claim, and what moved them.

    Confidence is how well corroborated the claim is and never changes on its
    own. Currency is how likely it still holds, computed at read time from
    the type's half-life -- so this endpoint needs a clock.
    """
    memory = _load(db, memory_id)
    now = reference_now(x_kivi_now)

    rows = list(
        db.scalars(
            select(MemoryEvidence)
            .where(MemoryEvidence.memory_id == memory.id)
            .order_by(MemoryEvidence.observed_at)
        )
    )
    supports = [row for row in rows if row.stance == "supports"]
    contradicts = [row for row in rows if row.stance == "contradicts"]

    current = ranking.currency(memory.type, memory.last_reinforced_at, now)
    replaced_by = (
        db.get(Memory, memory.superseded_by) if memory.superseded_by else None
    )

    return {
        "memory": {
            "id": str(memory.id),
            "type": memory.type,
            "content": memory.content,
            "status": memory.status,
        },
        "confidence": {
            "value": round(float(memory.posterior_mean or 0.0), 4),
            "alpha": round(float(memory.alpha), 3),
            "beta": round(float(memory.beta), 3),
            "supporting": _stance(supports),
            "contradicting": _stance(contradicts),
            "observations": memory.observation_count,
        },
        "currency": {
            "value": round(current, 4),
            "half_life_days": ranking.half_life(memory.type).days,
            "last_reinforced_at": _iso(memory.last_reinforced_at),
            "valid_until": _iso(memory.valid_until),
            "as_of": now.isoformat(),
        },
        "ranking_score": round(ranking.score(memory, now), 4),
        "use_count": memory.use_count,
        "superseded_by": (
            {"id": str(replaced_by.id), "content": replaced_by.content}
            if replaced_by
            else None
        ),
    }


def _stance(rows: list[MemoryEvidence]) -> dict:
    return {
        "count": len(rows),
        "weight": round(sum(float(row.weight) for row in rows), 3),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _load(db: Session, memory_id: uuid.UUID) -> Memory:
    memory = db.get(Memory, memory_id)
    if memory is None or memory.user_id != settings.default_user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such memory.")
    return memory
