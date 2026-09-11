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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.ask import reference_now
from app.config import settings
from app.db.session import get_db
from app.models.event import Event
from app.models.memory import Memory, MemoryEvidence
from app.models.trace import Trace
from app.services import memory_control, ranking

router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("")
def listing(
    db: Session = Depends(get_db),
    status_filter: str = Query(default="live", alias="status"),
    memory_type: str | None = Query(default=None, alias="type"),
    q: str | None = Query(default=None, description="substring of the claim"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_kivi_now: str | None = Header(default=None),
) -> dict:
    """Everything remembered, newest first.

    `status` defaults to "live" -- active and candidate, what the system
    currently holds. "superseded" is history, "all" is both. History is a
    separate ask rather than mixed in by default, because a list that puts
    a replaced price beside the current one, sorted by date, invites
    exactly the confusion the status field exists to prevent.

    Ordered by when the claim was last said, not when the row was written.
    A belief restated yesterday belongs at the top even if it was first
    formed in June.
    """
    now = reference_now(x_kivi_now)

    wanted = {
        "live": ["active", "candidate"],
        "active": ["active"],
        "candidate": ["candidate"],
        "superseded": ["superseded"],
        "all": ["active", "candidate", "superseded"],
    }.get(status_filter)
    if wanted is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "status must be one of live, active, candidate, superseded, all",
        )

    query = select(Memory).where(
        Memory.user_id == settings.default_user_id,
        Memory.status.in_(wanted),
    )
    if memory_type:
        query = query.where(Memory.type == memory_type)
    if q:
        query = query.where(Memory.content.ilike(f"%{q}%"))

    total = db.scalar(
        select(func.count()).select_from(query.subquery())
    ) or 0

    rows = list(
        db.scalars(
            query.order_by(Memory.last_reinforced_at.desc().nullslast())
            .offset(offset)
            .limit(limit)
        )
    )

    # One query for the whole page rather than one per row: a list of a
    # hundred beliefs should not be a hundred round trips to name their
    # replacements.
    replacements = {
        row.id: row.content
        for row in db.scalars(
            select(Memory).where(
                Memory.id.in_([m.superseded_by for m in rows if m.superseded_by])
            )
        )
    } if any(m.superseded_by for m in rows) else {}

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "counts": _counts(db),
        "memories": [_summary(row, now, replacements) for row in rows],
    }


def _counts(db: Session) -> dict[str, int]:
    """How many of each status, so the filters can show their own size."""
    return {
        state: count
        for state, count in db.execute(
            select(Memory.status, func.count())
            .where(Memory.user_id == settings.default_user_id)
            .group_by(Memory.status)
        )
    }


def _summary(memory: Memory, now: datetime, replacements: dict) -> dict:
    """One belief, as a list row.

    Confidence and currency both travel, because they answer different
    questions and a list showing only one of them is misleading either way:
    a well-corroborated claim from March and a shaky one from yesterday
    look identical if you print a single number.
    """
    return {
        "id": str(memory.id),
        "type": memory.type,
        "content": memory.content,
        "status": memory.status,
        "confidence": round(float(memory.posterior_mean or 0.0), 3),
        "currency": round(
            ranking.currency(memory.type, memory.last_reinforced_at, now), 3
        ),
        "stale": ranking.is_stale(memory, now),
        "times_said": memory.observation_count,
        "use_count": memory.use_count,
        "last_said": _iso(memory.last_reinforced_at),
        "valid_until": _iso(memory.valid_until),
        "subject": (memory.attributes or {}).get("subject"),
        "superseded_by": (
            {
                "id": str(memory.superseded_by),
                "content": replacements.get(memory.superseded_by),
            }
            if memory.superseded_by
            else None
        ),
    }


@router.delete("/{memory_id}", status_code=status.HTTP_200_OK)
def forget_one(memory_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Delete one belief, for real.

    The row goes, its evidence links go, its profile entry goes, and its
    vector goes -- a vector left behind keeps the text findable through the
    excerpt stored beside it, which would make this a lie told convincingly.

    The dictations stay. They are what the person actually said; deleting
    the record of speech because a conclusion drawn from it was wrong
    destroys the evidence rather than the belief.

    Anything this belief replaced is left pointing at nothing, which is
    handled by the column itself: the link is a foreign key that nulls on
    delete, so history loses a forwarding address rather than gaining a
    broken one.
    """
    memory = _load(db, memory_id)
    content = memory.content

    memory_control._purge(db, settings.default_user_id, [memory.id])
    db.commit()

    return {"deleted": str(memory_id), "content": content}


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
