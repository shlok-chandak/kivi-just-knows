"""Shared write-path logic for the API and the bulk importer.

Returns plain column values rather than ORM objects so both a single insert
and a batched upsert can use it, and neither can drift from the denylist.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from app.schemas.event import EventCreate
from app.services.denylist import denylist_reason, is_denylisted


def build_event_values(
    payload: EventCreate,
    user_id: uuid.UUID,
    source_batch_id: uuid.UUID | None = None,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    """Column values for one event, with the denylist applied.

    A denylisted app keeps its metadata but stores no content, so the ignore
    is auditable without retaining what was said.
    """
    denylisted = is_denylisted(payload.app)

    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "occurred_at": payload.occurred_at,
        "ingested_at": ingested_at or datetime.now(timezone.utc),
        "app": payload.app,
        "thread_id": payload.thread_id,
        "recipients": payload.recipients,
        "raw_asr": None if denylisted else payload.raw_asr,
        "formatted_text": None if denylisted else payload.formatted_text,
        "committed_text": None if denylisted else payload.committed_text,
        "asr_confidence": payload.asr_confidence,
        "duration_ms": payload.duration_ms,
        "ingest_status": "ignored" if denylisted else "pending",
        "ignore_reason": denylist_reason(payload.app) if denylisted else None,
        "source_batch_id": source_batch_id,
        "external_id": payload.external_id,
    }
