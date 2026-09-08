"""Request and response contracts for event ingestion."""

import hashlib
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

CONTEXT_HASH_LENGTH = 16


def hash_context(title: str) -> str:
    """Reduce a window title to an opaque grouping token.

    The title is never stored. It often contains a contact or document name,
    which this build deliberately does not keep, and its format differs per
    app -- so it is only ever compared for equality, never read.
    """
    digest = hashlib.sha256(title.strip().casefold().encode()).hexdigest()
    return digest[:CONTEXT_HASH_LENGTH]


class EventCreate(BaseModel):
    # Unknown keys are ignored so a foreign corpus can be posted unchanged.
    model_config = ConfigDict(extra="ignore")

    occurred_at: datetime
    raw_asr: NonEmptyStr
    formatted_text: NonEmptyStr

    app: str | None = None
    committed_text: str | None = None

    # Two ways to supply context, for the two kinds of client. A client that
    # would rather not send the title can hash it on the device and post
    # context_hash; anything else posts the title and we hash it here.
    window_title: str | None = None
    context_hash: str | None = None

    asr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    duration_ms: int | None = Field(default=None, ge=0)
    external_id: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Reject naive timestamps rather than guessing an offset.

        Every time-filtered query compares against this value, so an assumed
        offset would shift results silently and with no way to detect it.
        """
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "occurred_at must include a timezone offset, e.g. "
                "2026-06-14T17:04:11+05:30"
            )
        return value

    @model_validator(mode="after")
    def derive_context_hash(self) -> "EventCreate":
        """Hash the title when the client did not, then forget the title."""
        if self.context_hash is None and self.window_title:
            self.context_hash = hash_context(self.window_title)
        self.window_title = None
        return self


class EventRefused(BaseModel):
    """Returned when a dictation was not stored at all.

    Deliberately not an error: refusing is the system working as intended, and
    the client should not retry. It carries the category but never the text,
    so the response cannot become the copy we declined to keep.
    """

    status: str = "refused"
    category: str
    occurred_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    ingested_at: datetime
    app: str | None
    context_hash: str | None
    raw_asr: str | None
    formatted_text: str | None
    ingest_status: str
    ignore_reason: str | None
    consolidated_at: datetime | None
