"""Event ingestion endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
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


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.user_id != settings.default_user_id:
        raise HTTPException(status_code=404, detail="event not found")
    return event
