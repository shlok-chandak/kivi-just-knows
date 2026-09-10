"""One way in for everything a person can ask.

The work happens in the asking service, which replay shares. This route only
resolves the clock and hands the request over.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.services import asking

router = APIRouter(prefix="/ask", tags=["ask"])


class AskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000)
    # Text the person already has in front of them, for a restyle that acts
    # on a selection rather than on something retrieved.
    text: str | None = None


@router.post("")
def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    x_kivi_now: str | None = Header(default=None),
) -> dict:
    """Answer, find, restyle, draft, or edit memory -- whichever was asked."""
    result = asking.run(
        db,
        settings.default_user_id,
        payload.request,
        text=payload.text,
        now=reference_now(x_kivi_now),
    )
    db.commit()
    return result.as_dict()


def reference_now(header: str | None) -> datetime:
    """The clock every time expression resolves against.

    Injectable because the corpus sits in the past: resolved against the wall
    clock, "yesterday" is an empty range and the system abstains on a
    question it could have answered.
    """
    if not header:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(header)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
