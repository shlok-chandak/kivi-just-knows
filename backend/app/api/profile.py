"""Seeing and editing the always-on profile.

The profile goes into every answer, so it is the part of the system most
worth being able to inspect and correct. An entry the person removes stays
removed, and one they pin stays pinned.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.profile import STATES
from app.services import profile as profile_service

router = APIRouter(prefix="/profile", tags=["profile"])


class EntryPatch(BaseModel):
    memory_id: uuid.UUID
    state: str


def _entry(entry) -> dict:
    return {
        "memory_id": str(entry.memory.id),
        "type": entry.memory.type,
        "content": entry.memory.content,
        "score": round(entry.score, 3),
        "tokens": entry.tokens,
    }


@router.get("")
def read(db: Session = Depends(get_db)) -> dict:
    """What is currently sent with every question."""
    built = profile_service.load(db, settings.default_user_id)
    return {
        "style": [_entry(entry) for entry in built.style],
        "work": [_entry(entry) for entry in built.work],
        "tokens": built.tokens,
        "budget": profile_service.BUDGET_TOKENS,
        "rendered": built.rendered(),
    }


@router.patch("")
def edit(payload: EntryPatch, db: Session = Depends(get_db)) -> dict:
    """Pin an entry, hide it, or hand it back to the ranking."""
    if payload.state not in STATES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"state must be one of {list(STATES)}",
        )
    try:
        profile_service.set_state(
            db, settings.default_user_id, payload.memory_id, payload.state
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    db.commit()
    return read(db)


@router.post("/refresh")
def rebuild(db: Session = Depends(get_db)) -> dict:
    """Recompute now, rather than waiting for the next consolidation."""
    profile_service.refresh(db, settings.default_user_id)
    db.commit()
    return read(db)
