"""Shared write-path logic for the API and the bulk importer.

Returns plain column values rather than ORM objects so both a single insert
and a batched upsert can use it without duplicating the mapping.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from app.schemas.event import EventCreate


def build_event_values(
    payload: EventCreate,
    user_id: uuid.UUID,
    source_batch_id: uuid.UUID | None = None,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    """Column values for one event.

    Ingestion is unconditional: what the user said is stored as given.
    Filtering happens later, on the memory candidate rather than the event,
    so a dictation remains findable even when nothing is remembered from it.
    """
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "occurred_at": payload.occurred_at,
        "ingested_at": ingested_at or datetime.now(timezone.utc),
        "app": payload.app,
        "thread_id": payload.thread_id,
        "recipients": payload.recipients,
        "raw_asr": payload.raw_asr,
        "formatted_text": payload.formatted_text,
        "committed_text": payload.committed_text,
        "asr_confidence": payload.asr_confidence,
        "duration_ms": payload.duration_ms,
        "ingest_status": "pending",
        "ignore_reason": None,
        "source_batch_id": source_batch_id,
        "external_id": payload.external_id,
    }
