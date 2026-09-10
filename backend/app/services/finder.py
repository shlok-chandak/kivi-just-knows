"""Locating a specific dictation.

Different from recall in what it returns and in what decides the order.
Recall answers a question; this hands back the words themselves, because
"find the message I dictated at five" is asking for text, not a summary.

Filters do the work. Someone who says "yesterday, in Slack, around five" has
given three hard constraints, and the right answer is whatever sits inside
them -- ordered by time, not by how well it matches a topic they never
stated. Similarity only breaks ties, and only when a topic was given.

No model call. The parse already happened; this is a query.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event
from app.services import retrieval
from app.services.timeref import Range

logger = logging.getLogger("kivi.finder")

# Enough that the right one is in the list when the filters were loose,
# short enough to read.
LIMIT = 10


@dataclass
class Found:
    event: Event
    similarity: float | None
    matched_by: str

    def as_dict(self) -> dict:
        return {
            "event_id": str(self.event.id),
            "occurred_at": self.event.occurred_at.isoformat(),
            "app": self.event.app,
            "text": self.event.canonical_text,
            "raw_asr": self.event.raw_asr,
            "formatted_text": self.event.formatted_text,
            "edited": bool(
                self.event.committed_text
                and self.event.committed_text != self.event.formatted_text
            ),
            "similarity": (
                None if self.similarity is None else round(self.similarity, 3)
            ),
            "matched_by": self.matched_by,
        }


@dataclass
class FindResult:
    found: list[Found]
    filters: dict
    widened: list[str]

    @property
    def empty(self) -> bool:
        return not self.found


def find(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime,
    when: Range | None = None,
    apps: Sequence[str] | None = None,
    topic: str = "",
    limit: int = LIMIT,
) -> FindResult:
    """Dictations matching the stated constraints, newest first."""
    filters = {
        "time": when.expression if when else None,
        "apps": list(apps) if apps else [],
        "topic": topic or None,
    }
    widened: list[str] = []

    rows = _filtered(session, user_id, when=when, apps=apps, limit=limit)

    # Widen once, and say so. A silent widen turns "nothing in Slack
    # yesterday" into a confident answer about Gmail last week.
    if not rows and apps:
        rows = _filtered(session, user_id, when=when, apps=None, limit=limit)
        if rows:
            widened.append("dropped the app filter")

    if not rows and when is not None:
        rows = _filtered(session, user_id, when=None, apps=apps, limit=limit)
        if rows:
            widened.append(f"dropped the time filter ({when.expression})")

    if not rows:
        return FindResult([], filters, widened)

    # Without a topic there is nothing to rank by, so time order stands: the
    # constraints already said which dictations are meant.
    if not topic.strip():
        return FindResult(
            [Found(event, None, "filters") for event in rows], filters, widened
        )

    scored = retrieval.search_events(
        session,
        user_id,
        topic,
        now=now,
        since=when.start if when else None,
        until=when.end if when else None,
        apps=list(apps) if apps else None,
        unconsolidated_only=False,
        limit=limit,
    )
    by_id = {candidate.id: candidate.similarity for candidate in scored}

    if not (when is not None or apps) and scored:
        rows = _by_id(session, [candidate.id for candidate in scored])
    elif not (when is not None or apps) and not scored:
        widened.append("nothing matched the topic; showing the most recent")

    found = [
        Found(event, by_id.get(event.id), "filters+topic" if event.id in by_id else "filters")
        for event in rows
    ]
    # Ties broken by similarity, order otherwise still time. A dictation the
    # filters selected does not drop out for scoring badly on a topic the
    # person only mentioned in passing.
    found.sort(key=lambda item: (item.similarity or 0.0), reverse=True)
    return FindResult(found, filters, widened)


def _by_id(session: Session, ids: Sequence[uuid.UUID]) -> list[Event]:
    """The events the topic search picked, newest first before ranking."""
    if not ids:
        return []
    return list(
        session.scalars(
            select(Event)
            .where(Event.id.in_(list(ids)))
            .order_by(Event.occurred_at.desc())
        )
    )


def _filtered(
    session: Session,
    user_id: uuid.UUID,
    *,
    when: Range | None,
    apps: Sequence[str] | None,
    limit: int,
) -> list[Event]:
    """The hard filters, newest first."""
    query = select(Event).where(
        Event.user_id == user_id,
        Event.ingest_status != "ignored",
    )
    # Either end may be open: "before Friday" has no start.
    if when is not None and when.start is not None:
        query = query.where(Event.occurred_at >= when.start)
    if when is not None and when.end is not None:
        query = query.where(Event.occurred_at < when.end)
    if apps:
        query = query.where(Event.app.in_(list(apps)))

    return list(
        session.scalars(query.order_by(Event.occurred_at.desc()).limit(limit))
    )
