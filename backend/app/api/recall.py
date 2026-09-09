"""Asking Kivi something.

The one endpoint a person actually uses. Everything else in this service
exists so that this can answer from what was really said.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.services import recall

router = APIRouter(prefix="/recall", tags=["recall"])


class RecallRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    # Structured narrowing, supplied rather than parsed out of the question.
    # Reading "last week" from free text is its own problem, and guessing it
    # wrongly silently answers about the wrong days.
    since: datetime | None = None
    until: datetime | None = None
    apps: list[str] | None = None
    # Accepted because a question naming someone should still search for that
    # name. It is deliberately not a filter: recipients are not captured, and
    # the response says so rather than implying the narrowing happened.
    person: str | None = None


@router.post("")
def ask(payload: RecallRequest, db: Session = Depends(get_db)) -> dict:
    """Answer from this user's dictations, or say that nothing covers it."""
    result = recall.answer(
        db,
        _user_id(),
        payload.question,
        since=payload.since,
        until=payload.until,
        apps=payload.apps,
        person=payload.person,
    )
    # A use count was written, so the read path commits.
    db.commit()
    return result.as_dict()


def _user_id() -> uuid.UUID:
    """Single-user build: there is one owner and no authentication yet."""
    return settings.default_user_id
