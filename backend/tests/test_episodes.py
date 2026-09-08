"""Episode assembly: time-bounded, cross-app, and closed once.

Boundaries come from time only. The tests below fix the behaviour that made
the old per-conversation design wrong: a stretch of thinking that moves
between apps is one episode, and an episode that has been summarised is not
quietly left describing a smaller version of itself.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.config import settings
from app.db.session import SessionLocal
from app.models.episode import IDLE_GAP, MAX_EVENTS_PER_EPISODE, MAX_SPAN, Episode
from app.models.event import Event
from app.services.episodes import (
    assign_events,
    events_in_episode,
    partition_into_sittings,
)

USER = settings.default_user_id
START = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    db = SessionLocal()
    _clear(db)
    yield db
    _clear(db)
    db.close()


def _clear(db) -> None:
    db.execute(delete(Event).where(Event.user_id == USER))
    db.execute(delete(Episode).where(Episode.user_id == USER))
    db.commit()


def add(db, minutes: float, *, app="slack", context="ctx-a", text="We decided ₹299."):
    event = Event(
        id=uuid.uuid4(),
        user_id=USER,
        occurred_at=START + timedelta(minutes=minutes),
        ingested_at=START,
        app=app,
        context_hash=context,
        raw_asr=text.lower(),
        formatted_text=text,
        ingest_status="pending",
    )
    db.add(event)
    db.commit()
    return event


def episodes(db) -> list[Episode]:
    return sorted(
        db.query(Episode).filter(Episode.user_id == USER).all(),
        key=lambda e: e.started_at,
    )


# --- assembly ---------------------------------------------------------------


def test_events_close_together_form_one_episode(session):
    add(session, 0)
    add(session, 5)
    add(session, 10)

    assign_events(session, USER, now=START + timedelta(minutes=11))
    session.commit()

    assert len(episodes(session)) == 1
    assert episodes(session)[0].event_count == 3


def test_a_long_silence_starts_a_new_episode(session):
    add(session, 0)
    add(session, IDLE_GAP.total_seconds() / 60 + 5)

    assign_events(session, USER, now=START + timedelta(hours=3))
    session.commit()

    assert len(episodes(session)) == 2


def test_a_different_app_does_not_start_a_new_episode(session):
    """The point of the redesign: one train of thought crossing two apps."""
    add(session, 0, app="slack", context="ctx-a")
    add(session, 8, app="notion", context="ctx-b")

    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    assert len(episodes(session)) == 1
    assert episodes(session)[0].event_count == 2


def test_an_episode_stops_growing_at_the_maximum_span(session):
    minutes = 0.0
    while minutes <= MAX_SPAN.total_seconds() / 60 + 10:
        add(session, minutes)
        minutes += 10

    assign_events(session, USER, now=START + timedelta(hours=6))
    session.commit()

    for episode in episodes(session):
        assert episode.ended_at - episode.started_at <= MAX_SPAN
    assert len(episodes(session)) > 1


def test_an_episode_stops_growing_at_the_event_cap(session):
    for index in range(MAX_EVENTS_PER_EPISODE + 5):
        add(session, index * 0.5)

    assign_events(session, USER, now=START + timedelta(hours=6))
    session.commit()

    for episode in episodes(session):
        assert episode.event_count <= MAX_EVENTS_PER_EPISODE


def test_every_event_belongs_to_exactly_one_episode(session):
    """An event in no episode is unsearchable and breaks provenance."""
    for index in range(6):
        add(session, index * 4)

    assign_events(session, USER, now=START + timedelta(hours=2))
    session.commit()

    orphans = (
        session.query(Event)
        .filter(Event.user_id == USER, Event.episode_id.is_(None))
        .count()
    )
    assert orphans == 0


def test_ignored_events_are_still_assigned(session):
    """Content is stored either way, so it must remain reachable."""
    event = add(session, 0, text="Testing testing.")
    event.ingest_status = "ignored"
    event.ignore_reason = "no_content"
    session.commit()

    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    session.refresh(event)
    assert event.episode_id is not None


# --- closing ----------------------------------------------------------------


def test_a_recent_episode_stays_open(session):
    add(session, 0)
    result = assign_events(session, USER, now=START + timedelta(minutes=2))
    session.commit()

    assert episodes(session)[0].status == "open"
    assert result["needs_consolidation"] == []


def test_an_idle_episode_closes_and_is_queued(session):
    add(session, 0)
    result = assign_events(session, USER, now=START + IDLE_GAP + timedelta(minutes=1))
    session.commit()

    assert episodes(session)[0].status == "closed"
    assert result["needs_consolidation"] == [episodes(session)[0].id]


def test_an_already_summarised_episode_is_not_queued_again(session):
    add(session, 0)
    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    episode = episodes(session)[0]
    episode.summary_status = "verbatim"
    episode.summary = "We decided ₹299."
    session.commit()

    result = assign_events(session, USER, now=START + timedelta(hours=2))
    session.commit()
    assert result["needs_consolidation"] == []


# --- late arrivals ----------------------------------------------------------


def test_a_late_event_joins_the_stretch_it_happened_in(session):
    """An offline client flushing its queue must not create a duplicate."""
    add(session, 0)
    add(session, 10)
    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()
    assert len(episodes(session)) == 1

    add(session, 5)
    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    assert len(episodes(session)) == 1
    assert episodes(session)[0].event_count == 3


def test_a_grown_episode_is_summarised_again(session):
    """Otherwise the summary describes a smaller episode than it holds."""
    add(session, 0)
    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    episode = episodes(session)[0]
    episode.summary_status = "verbatim"
    episode.summary = "We decided ₹299."
    session.commit()

    add(session, 6)
    result = assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    episode = episodes(session)[0]
    assert episode.summary is None
    assert episode.summary_status is None
    assert result["needs_consolidation"] == [episode.id]


def test_a_late_event_clears_consolidation_on_its_episode(session):
    """Its claims were drawn from a smaller set, so they must be redrawn."""
    event = add(session, 0)
    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    episode = episodes(session)[0]
    episode.summary_status = "generated"
    event.consolidated_at = START
    session.commit()

    add(session, 4)
    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    session.refresh(event)
    assert event.consolidated_at is None


# --- reproducibility --------------------------------------------------------


def test_assignment_is_idempotent(session):
    for index in range(5):
        add(session, index * 6)

    assign_events(session, USER, now=START + timedelta(hours=2))
    session.commit()
    before = [(e.started_at, e.ended_at, e.event_count) for e in episodes(session)]

    assign_events(session, USER, now=START + timedelta(hours=2))
    session.commit()
    after = [(e.started_at, e.ended_at, e.event_count) for e in episodes(session)]

    assert before == after


def test_events_come_back_in_time_order(session):
    add(session, 8)
    add(session, 0)
    add(session, 4)

    assign_events(session, USER, now=START + timedelta(hours=1))
    session.commit()

    ordered = events_in_episode(session, episodes(session)[0].id)
    assert [e.occurred_at for e in ordered] == sorted(e.occurred_at for e in ordered)


# --- sittings (pure, never persisted) ---------------------------------------


class _Fake:
    def __init__(self, minutes, app, context):
        self.occurred_at = START + timedelta(minutes=minutes)
        self.app = app
        self.context_hash = context


def test_a_context_change_starts_a_new_sitting():
    events = [_Fake(0, "slack", "a"), _Fake(1, "slack", "b")]
    assert len(partition_into_sittings(events)) == 2


def test_a_shared_context_holds_a_sitting_together_for_half_an_hour():
    events = [_Fake(0, "slack", "a"), _Fake(25, "slack", "a")]
    assert len(partition_into_sittings(events)) == 1


def test_without_a_context_five_minutes_splits_the_sitting():
    """Three people on one app in one window are not one conversation."""
    events = [_Fake(0, "slack", None), _Fake(8, "slack", None)]
    assert len(partition_into_sittings(events)) == 2


def test_without_a_context_close_events_stay_together():
    events = [_Fake(0, "slack", None), _Fake(3, "slack", None)]
    assert len(partition_into_sittings(events)) == 1


def test_no_events_means_no_sittings():
    assert partition_into_sittings([]) == []
