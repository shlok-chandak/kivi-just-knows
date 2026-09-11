"""Event ingestion endpoints, and following one dictation through."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.embedding import Embedding
from app.models.episode import (
    IDLE_GAP,
    MAX_EVENTS_PER_EPISODE,
    MAX_SPAN,
    Episode,
)
from app.models.event import Event
from app.models.memory import Memory, MemoryEvidence
from app.models.rejected import RejectedCandidate
from app.schemas.event import EventCreate, EventOut, EventRefused
from app.services import queue
from app.services.apps import normalise_app
from app.services.episodes import ASSIGN_SUBJECT
from app.services.ingest import (
    build_event_values,
    find_previous,
    refuse_if_sensitive,
)
from app.worker.handlers import STAGE_EMBED, STAGE_EPISODE_ASSIGN

router = APIRouter(prefix="/events", tags=["events"])


@router.post(
    "",
    response_model=EventOut | EventRefused,
    status_code=status.HTTP_201_CREATED,
)
def create_event(
    payload: EventCreate, response: Response, db: Session = Depends(get_db)
) -> Event | EventRefused:
    user_id = settings.default_user_id

    # Refusal comes first: nothing else should touch a dictation we are not
    # going to keep. 200 rather than 201, because nothing was created, and not
    # an error, because refusing is the system working as intended.
    category = refuse_if_sensitive(db, payload, user_id)
    if category is not None:
        db.commit()
        response.status_code = status.HTTP_200_OK
        return EventRefused(category=category, occurred_at=payload.occurred_at)

    # Needed before the insert: a repeat is only a retry relative to what came
    # immediately before it in the same place.
    previous = find_previous(
        db,
        user_id=user_id,
        app=normalise_app(payload.app) or None,
        context_hash=payload.context_hash,
        before=payload.occurred_at,
    )

    event = Event(
        **build_event_values(payload, user_id=user_id, previous=previous)
    )

    db.add(event)
    db.commit()
    db.refresh(event)

    # Enqueued even when ignored: every event must belong to an episode, or
    # it is unreachable and the provenance chain has a hole. Cost is gated
    # later -- an episode with nothing extractable never reaches a model.
    for stage in (STAGE_EMBED, STAGE_EPISODE_ASSIGN):
        queue.enqueue(
            db,
            user_id=event.user_id,
            stage=stage,
            subject_key=ASSIGN_SUBJECT,
        )
    db.commit()

    return event


class BatchIn(BaseModel):
    """A whole corpus in one request."""

    events: list[EventCreate] = Field(min_length=1, max_length=2000)


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
def create_events(payload: BatchIn, db: Session = Depends(get_db)) -> dict:
    """Ingest many dictations at once.

    Every record goes through the same path as a single POST -- refusal
    first, then the previous-dictation lookup a retry is judged against --
    because a corpus loaded in bulk that skipped either check would be
    stored on terms no single dictation is ever stored on.

    Returns counts only. What happened to each one is worth watching rather
    than reading, and /stream/ingest is where that happens.
    """
    user_id = settings.default_user_id
    stored = refused = 0

    for record in payload.events:
        category = refuse_if_sensitive(db, record, user_id)
        if category is not None:
            refused += 1
            continue

        previous = find_previous(
            db,
            user_id=user_id,
            app=normalise_app(record.app) or None,
            context_hash=record.context_hash,
            before=record.occurred_at,
        )
        db.add(Event(**build_event_values(record, user_id=user_id, previous=previous)))
        stored += 1
        # Flushed per record so the next one's previous-dictation lookup can
        # see it, which is what makes a retry inside the batch detectable.
        db.flush()

    # One assignment job for the batch, not one per record: the queue
    # coalesces by subject anyway, and the worker walks every loose event.
    for stage in (STAGE_EMBED, STAGE_EPISODE_ASSIGN):
        queue.enqueue(db, user_id=user_id, stage=stage, subject_key=ASSIGN_SUBJECT)
    db.commit()

    return {
        "received": len(payload.events),
        "stored": stored,
        "refused": refused,
        "watch": "/stream/ingest",
    }


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.user_id != settings.default_user_id:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.get("/{event_id}/journey")
def journey(event_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Where one dictation got to, and what is still ahead of it.

    A dictation does not finish when it is stored. It waits for an episode
    to close, then for that episode to be read, and only then does it
    become a belief or get declined. Every one of those is a place it can
    stop, and none of them is visible from the row itself.

    Polled while an episode is open, which is why the close prediction is
    part of the answer: "waiting" with no idea what it is waiting for is
    the state this endpoint exists to remove.
    """
    event = db.get(Event, event_id)
    if event is None or event.user_id != settings.default_user_id:
        raise HTTPException(status_code=404, detail="event not found")

    episode = db.get(Episode, event.episode_id) if event.episode_id else None

    memories = list(
        db.execute(
            select(Memory, MemoryEvidence)
            .join(MemoryEvidence, MemoryEvidence.memory_id == Memory.id)
            .where(MemoryEvidence.event_id == event.id)
        )
    )

    declined = list(
        db.scalars(
            select(RejectedCandidate).where(
                RejectedCandidate.user_id == event.user_id,
                RejectedCandidate.event_id == event.id,
            )
        )
    )

    embedded = db.scalar(
        select(func.count())
        .select_from(Embedding)
        .where(
            Embedding.object_type == "event",
            Embedding.object_id == event.id,
            Embedding.model == settings.embedding_model,
        )
    ) or 0

    return {
        "event": {
            "id": str(event.id),
            "occurred_at": event.occurred_at.isoformat(),
            "app": event.app,
            "text": event.canonical_text,
            "raw_asr": event.raw_asr,
            "formatted_text": event.formatted_text,
            "committed_text": event.committed_text,
            "edited": bool(
                event.committed_text
                and event.committed_text != event.formatted_text
            ),
            "ingest_status": event.ingest_status,
            "ignore_reason": event.ignore_reason,
            "embedded": bool(embedded),
            "consolidated_at": (
                event.consolidated_at.isoformat() if event.consolidated_at else None
            ),
        },
        "episode": _episode_state(db, episode),
        "memories": [
            {
                "id": str(memory.id),
                "content": memory.content,
                "type": memory.type,
                "status": memory.status,
                "confidence": round(float(memory.posterior_mean or 0.0), 3),
                "excerpt": evidence.excerpt,
                "weight": round(float(evidence.weight), 2),
            }
            for memory, evidence in memories
        ],
        "declined": [
            {
                "rule": row.rejection_rule,
                "rationale": row.rationale,
                # Same withholding as everywhere else: showing the content
                # in order to explain the refusal would undo it.
                "candidate": (
                    None if row.rejection_rule in _WITHHELD else row.candidate
                ),
                "withheld": row.rejection_rule in _WITHHELD,
            }
            for row in declined
        ],
    }


_WITHHELD = ("sensitive_category", "sensitive_on_review")


def _episode_state(db: Session, episode: Episode | None) -> dict | None:
    """The episode this dictation is sitting in, and when it will close.

    Three things can close an episode and the soonest one wins, so the
    prediction names which. Reported rather than computed in the browser
    because the thresholds live here -- a copy in the frontend would be
    right until somebody changed one.
    """
    if episode is None:
        return None

    state: dict = {
        "id": str(episode.id),
        "status": episode.status,
        "event_count": episode.event_count,
        "started_at": episode.started_at.isoformat(),
        "ended_at": episode.ended_at.isoformat(),
        "summary_status": episode.summary_status,
        "title": episode.title,
        "summary": episode.summary,
        "topic_tags": episode.topic_tags,
        "limits": {
            "idle_minutes": IDLE_GAP.total_seconds() / 60,
            "max_span_hours": MAX_SPAN.total_seconds() / 3600,
            "max_events": MAX_EVENTS_PER_EPISODE,
        },
    }

    if episode.status == "closed":
        state["closes"] = None
        return state

    now = datetime.now(timezone.utc)
    candidates = [
        (episode.ended_at + IDLE_GAP, "nothing else said for 20 minutes"),
        (episode.started_at + MAX_SPAN, "the stretch has run two hours"),
    ]
    if episode.event_count >= MAX_EVENTS_PER_EPISODE:
        candidates.append((now, "it is full"))

    at, because = min(candidates)
    state["closes"] = {
        "at": at.isoformat(),
        "because": because,
        "in_seconds": max(int((at - now).total_seconds()), 0),
        # Each new dictation pushes the idle deadline out, so a number
        # counting down is only true until the next one lands.
        "resets_on_next": because.startswith("nothing else"),
    }
    return state
