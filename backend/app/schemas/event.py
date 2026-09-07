"""Request and response contracts for event ingestion."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EventCreate(BaseModel):
    # Unknown keys are ignored so a foreign corpus can be posted unchanged.
    model_config = ConfigDict(extra="ignore")

    occurred_at: datetime
    raw_asr: NonEmptyStr
    formatted_text: NonEmptyStr

    app: str | None = None
    thread_id: str | None = None
    recipients: list[str] | None = None
    committed_text: str | None = None

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

    @field_validator("recipients")
    @classmethod
    def clean_recipients(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [name.strip() for name in value if name and name.strip()]


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    ingested_at: datetime
    app: str | None
    thread_id: str | None
    recipients: list[str] | None
    raw_asr: str | None
    formatted_text: str | None
    ingest_status: str
    ignore_reason: str | None
