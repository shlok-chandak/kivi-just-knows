"""Shared write-path logic for the API and the bulk importer.

Returns plain column values rather than ORM objects so both a single insert
and a batched upsert can use it without duplicating the mapping.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event import Event
from app.models.rejected import RejectedCandidate
from app.schemas.event import EventCreate
from app.services.apps import normalise_app
from app.services.junk import classify
from app.services.sensitive import REJECTION_RULE, detect_sensitive


class PreviousDictation(NamedTuple):
    """The dictation immediately before this one, in the same context."""

    text: str
    occurred_at: datetime


def find_previous(
    session: Session,
    user_id: uuid.UUID,
    app: str | None,
    context_hash: str | None,
    before: datetime,
) -> PreviousDictation | None:
    """The last dictation in the same place, for retry detection.

    Scoped to the same app and context: the same sentence said in two
    different conversations is a repetition, not a failed attempt.
    """
    row = session.execute(
        select(Event.formatted_text, Event.occurred_at)
        .where(
            Event.user_id == user_id,
            Event.app == app,
            Event.context_hash == context_hash,
            Event.occurred_at < before,
            Event.formatted_text.is_not(None),
        )
        .order_by(Event.occurred_at.desc())
        .limit(1)
    ).first()

    if row is None:
        return None
    return PreviousDictation(text=row[0], occurred_at=row[1])


def refuse_if_sensitive(
    session: Session,
    payload: EventCreate,
    user_id: uuid.UUID,
) -> str | None:
    """The category this dictation was refused for, or None to store it.

    Checks every text the dictation carries, not just the one we would extract
    from: the raw transcript can hold a password that the formatter tidied
    away, and refusing on the tidied version only would miss it.

    A refusal row is written so the user can see that something was declined
    at this time in this app. The content is not recorded -- it is the thing
    being refused.
    """
    if settings.store_sensitive_content:
        return None

    for text in (payload.raw_asr, payload.formatted_text, payload.committed_text):
        category = detect_sensitive(text)
        if category is None:
            continue

        session.add(
            RejectedCandidate(
                user_id=user_id,
                app=normalise_app(payload.app) or None,
                occurred_at=payload.occurred_at,
                candidate=None,
                rejection_rule=REJECTION_RULE,
                rationale=f"refused at ingest: {category}",
            )
        )
        return category

    return None


def build_event_values(
    payload: EventCreate,
    user_id: uuid.UUID,
    source_batch_id: uuid.UUID | None = None,
    ingested_at: datetime | None = None,
    previous: PreviousDictation | None = None,
) -> dict[str, Any]:
    """Column values for one event.

    Content is stored either way. The gate decides only whether the event is
    worth embedding, so a dictation stays findable by time and app even when
    nothing is remembered from it.
    """
    gap = None
    if previous is not None:
        gap = (payload.occurred_at - previous.occurred_at).total_seconds()

    reason = classify(
        payload.formatted_text,
        committed_text=payload.committed_text,
        asr_confidence=payload.asr_confidence,
        duration_ms=payload.duration_ms,
        previous_text=previous.text if previous else None,
        seconds_since_previous=gap,
    )

    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "occurred_at": payload.occurred_at,
        "ingested_at": ingested_at or datetime.now(timezone.utc),
        # Normalised once, here. Nothing downstream re-derives it, and no SQL
        # has to reimplement the same rule to read it back.
        "app": normalise_app(payload.app) or None,
        "context_hash": payload.context_hash,
        "raw_asr": payload.raw_asr,
        "formatted_text": payload.formatted_text,
        "committed_text": payload.committed_text,
        "asr_confidence": payload.asr_confidence,
        "duration_ms": payload.duration_ms,
        "consolidated_at": None,
        "ingest_status": "ignored" if reason else "pending",
        "ignore_reason": reason,
        "source_batch_id": source_batch_id,
        "external_id": payload.external_id,
    }
