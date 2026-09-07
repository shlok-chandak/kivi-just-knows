"""Event ingestion endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.event import Event
from app.schemas.event import EventCreate, EventOut
from app.services.ingest import build_event_values

router = APIRouter(prefix="/events", tags=["events"])


@router.post("", response_model=EventOut, status_code=status.HTTP_201_CREATED)
def create_event(payload: EventCreate, db: Session = Depends(get_db)) -> Event:
    event = Event(**build_event_values(payload, user_id=settings.default_user_id))

    db.add(event)
    db.commit()
    db.refresh(event)

    # TODO(M2): enqueue the ingest pipeline job when ingest_status == 'pending'.
    return event


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)) -> Event:
    event = db.get(Event, event_id)
    if event is None or event.user_id != settings.default_user_id:
        raise HTTPException(status_code=404, detail="event not found")
    return event
