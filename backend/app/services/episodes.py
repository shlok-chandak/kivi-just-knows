"""Episode assembly: grouping events into stretches of activity.

Assembly is append-only. Each unassigned event either extends a nearby
episode or starts a new one, and an episode closes when the user has been
idle long enough or the stretch has run too long. Nothing here consults a
model, and nothing is re-partitioned from scratch.

Reproducibility is a property of boundaries and membership, not of row ids:
two imports of the same corpus produce the same episodes over the same
events. Episode ids are random, deliberately -- deriving an id from a member
event is what previously made identity depend on which event happened to
arrive first.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.episode import (
    IDLE_GAP,
    MAX_EVENTS_PER_EPISODE,
    MAX_SPAN,
    SITTING_GAP_APP_ONLY,
    SITTING_GAP_WITH_CONTEXT,
    Episode,
)
from app.models.event import Event

# Assignment is not scoped to a conversation any more, so there is one
# subject per user and pending jobs coalesce onto it.
ASSIGN_SUBJECT = "events"


def events_in_episode(session: Session, episode_id: uuid.UUID) -> list[Event]:
    """An episode's events, oldest first.

    Ordered by (occurred_at, id): the id breaks ties so two events sharing a
    timestamp always sort the same way, which reproducibility depends on.
    """
    return list(
        session.scalars(
            select(Event)
            .where(Event.episode_id == episode_id)
            .order_by(Event.occurred_at, Event.id)
        )
    )


def partition_into_sittings(events: list[Event]) -> list[list[Event]]:
    """Split an episode's events into sittings, for prompt structure only.

    Pure, and never persisted. A sitting is a run of events in the same app
    and the same window context, close together in time. The gap depends on
    how much evidence there is for grouping: a shared context is holding the
    conversation together, whereas app alone is weak and needs proximity --
    half an hour of app-only grouping would merge unrelated conversations.
    """
    sittings: list[list[Event]] = []
    current: list[Event] = []

    for event in events:
        if current:
            previous = current[-1]
            same_place = (
                event.app == previous.app
                and event.context_hash == previous.context_hash
            )
            limit = (
                SITTING_GAP_WITH_CONTEXT
                if event.context_hash
                else SITTING_GAP_APP_ONLY
            )
            if not same_place or event.occurred_at - previous.occurred_at > limit:
                sittings.append(current)
                current = []
        current.append(event)

    if current:
        sittings.append(current)
    return sittings


def _episode_for(
    session: Session, user_id: uuid.UUID, occurred_at: datetime
) -> Episode | None:
    """The episode this event belongs to, if one is near enough.

    Adjacency is checked at both ends so an event that arrives late -- an
    offline client flushing its queue -- joins the stretch it actually
    happened in rather than starting a duplicate episode overlapping it.
    """
    candidate = session.scalars(
        select(Episode)
        .where(
            Episode.user_id == user_id,
            Episode.started_at - IDLE_GAP <= occurred_at,
            Episode.ended_at + IDLE_GAP >= occurred_at,
            # A stretch closed by hand stays closed. The time rules still
            # absorb a late take, because a client flushing its queue is
            # describing when things happened; a person clicking "finish"
            # is saying that stretch is over.
            Episode.closed_by.is_distinct_from("hand"),
        )
        .order_by(Episode.started_at.desc())
        .limit(1)
    ).first()

    if candidate is None:
        return None

    started = min(candidate.started_at, occurred_at)
    ended = max(candidate.ended_at, occurred_at)
    if ended - started > MAX_SPAN:
        return None
    if candidate.event_count >= MAX_EVENTS_PER_EPISODE:
        return None
    return candidate


def _refresh(session: Session, episode: Episode) -> None:
    """Recompute an episode's bounds from its events.

    Derived from the rows rather than accumulated, so attaching a late event
    cannot leave the range disagreeing with what the episode contains.
    """
    started, ended, count = session.execute(
        select(
            func.min(Event.occurred_at),
            func.max(Event.occurred_at),
            func.count(),
        ).where(Event.episode_id == episode.id)
    ).one()

    if count:
        episode.started_at = started
        episode.ended_at = ended
    episode.event_count = count


def _should_close(episode: Episode, now: datetime) -> str | None:
    """Which rule closes this episode, if any.

    Returns the reason rather than a bool so it can be stored: an episode
    that ran out of room and one the user walked away from are different
    facts, and only the record distinguishes them afterwards.
    """
    if episode.event_count >= MAX_EVENTS_PER_EPISODE:
        return "full"
    if episode.ended_at - episode.started_at >= MAX_SPAN:
        return "span"
    if now - episode.ended_at > IDLE_GAP:
        return "idle"
    return None


def assign_events(
    session: Session,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Attach unassigned events to episodes, then close the ones that are done."""
    now = now or datetime.now(timezone.utc)

    pending = list(
        session.scalars(
            select(Event)
            .where(Event.user_id == user_id, Event.episode_id.is_(None))
            .order_by(Event.occurred_at, Event.id)
        )
    )

    touched: dict[uuid.UUID, Episode] = {}

    for event in pending:
        episode = _episode_for(session, user_id, event.occurred_at)
        if episode is None:
            episode = Episode(
                id=uuid.uuid4(),
                user_id=user_id,
                started_at=event.occurred_at,
                ended_at=event.occurred_at,
                event_count=0,
                status="open",
            )
            session.add(episode)
            session.flush()

        event.episode_id = episode.id
        # Keep the in-memory bounds current so the next event in this batch
        # is measured against where the episode now ends.
        episode.started_at = min(episode.started_at, event.occurred_at)
        episode.ended_at = max(episode.ended_at, event.occurred_at)
        episode.event_count += 1
        touched[episode.id] = episode

    session.flush()

    # An episode that already had a summary and has since gained events must
    # be summarised again: the old summary describes a smaller episode.
    needs_consolidation: list[uuid.UUID] = []
    closed_count = 0

    for episode in touched.values():
        _refresh(session, episode)
        if episode.summary_status is None:
            continue

        episode.title = None
        episode.summary = None
        episode.topic_tags = None
        episode.summary_status = None
        session.execute(
            update(Event)
            .where(Event.episode_id == episode.id)
            .values(consolidated_at=None)
        )
        # A closed episode is not revisited by the loop below, so queueing it
        # here is what stops a cleared summary from never being rewritten.
        if episode.status == "closed":
            needs_consolidation.append(episode.id)

    # Any episode still open may have become idle since the last pass, even
    # if no new event touched it.
    for episode in session.scalars(
        select(Episode).where(Episode.user_id == user_id, Episode.status == "open")
    ):
        reason = _should_close(episode, now)
        if reason:
            episode.status = "closed"
            episode.closed_by = reason
            closed_count += 1
            if episode.summary_status is None:
                needs_consolidation.append(episode.id)

    session.flush()
    return {
        "assigned": len(pending),
        "episodes_touched": len(touched),
        "closed": closed_count,
        "needs_consolidation": needs_consolidation,
    }
