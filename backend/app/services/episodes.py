"""Deterministic episode assignment.

An episode is one sitting: a continuous stretch of dictation in one app and
one thread. Boundaries come from time and place only -- never from a model,
never from semantic similarity, which would make assignment depend on
ingest order and destroy the time and participant filters retrieval needs.

Assignment rebuilds a whole group from its sorted events, so the result is a
pure function of the event set. Two runs over the same data agree exactly,
including on episode ids.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.episode import (
    MAX_EVENTS_PER_EPISODE,
    MAX_GAP_MINUTES,
    Episode,
    EpisodeEvent,
)
from app.models.event import Event
from app.services.apps import normalise_app

# Fixed namespace for deriving episode ids. Changing it re-keys every episode.
EPISODE_NAMESPACE = uuid.UUID("8f3a1c52-6b7d-4e19-9c84-2d0f5a7b1e33")

MAX_GAP = timedelta(minutes=MAX_GAP_MINUTES)


def group_key(app: str | None, thread_id: str | None, event_id: uuid.UUID) -> str:
    """Which sitting-group an event belongs to.

    Thread when available, else app, else the event alone. Recipient plays no
    part: it is not captured, and an unreliable grouping key would silently
    merge unrelated conversations.
    """
    canonical = normalise_app(app)

    if thread_id:
        return f"t:{canonical}:{thread_id}"
    if canonical:
        return f"a:{canonical}"
    return f"e:{event_id}"


def group_key_for(event: Event) -> str:
    return group_key(event.app, event.thread_id, event.id)


def group_keys_for(events: list[Event]) -> list[str]:
    """Distinct group keys touched by these events, in first-seen order."""
    return list(dict.fromkeys(group_key_for(event) for event in events))


def _normalised_app_column():
    """SQL equivalent of normalise_app, for filtering by canonical name."""
    return func.replace(
        func.replace(func.lower(func.coalesce(Event.app, "")), " ", "_"), "-", "_"
    )


def events_in_group(
    session: Session, user_id: uuid.UUID, group_key: str
) -> list[Event]:
    """Every event in a group, oldest first.

    Ordered by (occurred_at, id): the id breaks ties so two events sharing a
    timestamp always sort the same way, which reproducibility depends on.
    """
    kind, _, rest = group_key.partition(":")
    query = select(Event).where(Event.user_id == user_id)

    if kind == "t":
        app, _, thread_id = rest.partition(":")
        query = query.where(
            Event.thread_id == thread_id, _normalised_app_column() == app
        )
    elif kind == "a":
        query = query.where(
            Event.thread_id.is_(None), _normalised_app_column() == rest
        )
    elif kind == "e":
        query = query.where(Event.id == uuid.UUID(rest))
    else:
        raise ValueError(f"unrecognised group key: {group_key!r}")

    return list(session.scalars(query.order_by(Event.occurred_at, Event.id)))


def partition(events: list[Event]) -> list[list[Event]]:
    """Split time-ordered events into sittings.

    Pure: no clock, no database. A new sitting starts when the gap from the
    previous event exceeds the window, or when the current one is full.
    """
    sittings: list[list[Event]] = []
    current: list[Event] = []

    for event in events:
        if current:
            gap = event.occurred_at - current[-1].occurred_at
            if gap > MAX_GAP or len(current) >= MAX_EVENTS_PER_EPISODE:
                sittings.append(current)
                current = []
        current.append(event)

    if current:
        sittings.append(current)
    return sittings


def episode_id_for(
    user_id: uuid.UUID, group_key: str, first_event_id: uuid.UUID
) -> uuid.UUID:
    """Derive a stable id, so rebuilding produces the same episode rows."""
    return uuid.uuid5(EPISODE_NAMESPACE, f"{user_id}|{group_key}|{first_event_id}")


def _is_closed(sitting: list[Event], is_last: bool, now: datetime) -> bool:
    """Only the most recent sitting can still be open.

    Earlier ones are closed by definition: a later sitting exists only because
    the gap rule already fired.
    """
    if not is_last:
        return True
    if len(sitting) >= MAX_EVENTS_PER_EPISODE:
        return True
    return (now - sitting[-1].occurred_at) > MAX_GAP


def rebuild_group(
    session: Session,
    user_id: uuid.UUID,
    group_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recompute every episode in a group from scratch.

    Deleting first is what keeps this idempotent: a late event that bridges
    two sittings produces one merged episode rather than a stale split.
    """
    now = now or datetime.now(timezone.utc)
    events = events_in_group(session, user_id, group_key)

    # Summaries cost a model call, so carry them across the rebuild. Episode
    # ids are derived from their contents, which means an unchanged sitting
    # keeps its id and can keep its summary; a genuinely changed one gets a
    # new id and is resummarised, which is correct.
    preserved = {
        row.id: row
        for row in session.scalars(
            select(Episode).where(
                Episode.user_id == user_id,
                Episode.group_key == group_key,
                Episode.summary_status.is_not(None),
            )
        )
    }
    carried = {
        episode_id: (row.title, row.summary, row.summary_status, row.topic_tags)
        for episode_id, row in preserved.items()
    }

    session.execute(
        delete(Episode).where(
            Episode.user_id == user_id, Episode.group_key == group_key
        )
    )

    if not events:
        return {
            "group_key": group_key,
            "episodes": 0,
            "events": 0,
            "open": 0,
            "needs_summary": [],
        }

    sittings = partition(events)
    open_count = 0
    needs_summary: list[uuid.UUID] = []

    for index, sitting in enumerate(sittings):
        first, last = sitting[0], sitting[-1]
        closed = _is_closed(sitting, index == len(sittings) - 1, now)
        open_count += 0 if closed else 1

        episode_id = episode_id_for(user_id, group_key, first.id)
        title, summary, summary_status, topic_tags = carried.get(
            episode_id, (None, None, None, None)
        )

        episode = Episode(
            id=episode_id,
            user_id=user_id,
            group_key=group_key,
            started_at=first.occurred_at,
            ended_at=last.occurred_at,
            app=first.app,
            thread_id=first.thread_id,
            event_count=len(sitting),
            status="closed" if closed else "open",
            title=title,
            summary=summary,
            summary_status=summary_status,
            topic_tags=topic_tags,
        )
        session.add(episode)
        session.flush()

        session.add_all(
            EpisodeEvent(episode_id=episode.id, event_id=event.id, seq=seq)
            for seq, event in enumerate(sitting)
        )

        # Only closed episodes are summarised: an open one is still growing,
        # and would be resummarised on every new event.
        if closed and summary_status is None:
            needs_summary.append(episode_id)

    session.flush()
    return {
        "group_key": group_key,
        "episodes": len(sittings),
        "events": len(events),
        "open": open_count,
        "needs_summary": needs_summary,
    }
