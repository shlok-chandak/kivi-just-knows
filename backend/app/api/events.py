"""Event ingestion endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.event import Event
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
