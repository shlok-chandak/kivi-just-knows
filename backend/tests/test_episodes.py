"""Episode assignment: boundary rules, grouping, and reproducibility."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.config import settings
from app.db.session import SessionLocal
from app.models.episode import MAX_EVENTS_PER_EPISODE, Episode, EpisodeEvent
from app.models.event import Event
from app.models.job import Job
from app.services.episodes import (
    group_key,
    group_keys_for,
    partition,
    rebuild_group,
)

BASE = datetime(2026, 9, 4, 11, 0, tzinfo=timezone.utc)
FIXTURE = "corpus/fixture.jsonl"


@dataclass
class FakeEvent:
    """Only what partition reads: a timestamp and an id."""

    occurred_at: datetime
    id: uuid.UUID


def at(minutes: int) -> FakeEvent:
    return FakeEvent(BASE + timedelta(minutes=minutes), uuid.uuid4())


# --- group key --------------------------------------------------------------


def test_thread_wins_when_present():
    eid = uuid.uuid4()
    assert group_key("slack", "slack:dm:aditya", eid) == "t:slack:slack:dm:aditya"


def test_app_only_when_thread_missing():
    assert group_key("notion", None, uuid.uuid4()) == "a:notion"


def test_event_is_its_own_group_when_app_missing():
    eid = uuid.uuid4()
    assert group_key(None, None, eid) == f"e:{eid}"


@pytest.mark.parametrize("spelling", ["1Password", "1password", "1 Password", "1-password"])
def test_app_spellings_group_together(spelling):
    assert group_key(spelling, None, uuid.uuid4()) == "a:1password"


def test_group_keys_are_deduplicated_in_order():
    a, b = FakeEvent(BASE, uuid.uuid4()), FakeEvent(BASE, uuid.uuid4())
    events = [
        Event(app="slack", thread_id="t1", id=a.id),
        Event(app="notion", thread_id=None, id=b.id),
        Event(app="slack", thread_id="t1", id=uuid.uuid4()),
    ]
    assert group_keys_for(events) == ["t:slack:t1", "a:notion"]


# --- partition --------------------------------------------------------------


def test_empty_input_yields_no_episodes():
    assert partition([]) == []


def test_events_inside_the_window_are_one_sitting():
    events = [at(0), at(5), at(29)]
    assert [len(s) for s in partition(events)] == [3]


def test_a_gap_over_thirty_minutes_splits():
    events = [at(0), at(31)]
    assert [len(s) for s in partition(events)] == [1, 1]


def test_exactly_thirty_minutes_does_not_split():
    """The rule is gap > 30, so the boundary itself stays together."""
    assert [len(s) for s in partition([at(0), at(30)])] == [2]


def test_the_window_is_measured_from_the_previous_event():
    """A steady stream never splits, however long it runs."""
    events = [at(minute) for minute in range(0, 200, 20)]
    assert [len(s) for s in partition(events)] == [10]


def test_the_event_cap_splits_a_continuous_stream():
    events = [at(minute) for minute in range(40)]
    sizes = [len(s) for s in partition(events)]
    assert sizes == [MAX_EVENTS_PER_EPISODE, 40 - MAX_EVENTS_PER_EPISODE]


def test_partition_is_pure():
    """Same input, same output, no clock and no database involved."""
    events = [at(0), at(5), at(90), at(95)]
    assert partition(events) == partition(events)


# --- rebuild ----------------------------------------------------------------


def import_fixture() -> None:
    from scripts.import_corpus import main

    main([FIXTURE, "--truncate"])


def assign_all(now: datetime) -> None:
    session = SessionLocal()
    try:
        events = list(
            session.scalars(
                select(Event).where(Event.user_id == settings.default_user_id)
            )
        )
        for key in group_keys_for(events):
            rebuild_group(session, settings.default_user_id, key, now=now)
        session.commit()
    finally:
        session.close()


def snapshot() -> tuple[list, list]:
    session = SessionLocal()
    try:
        episodes = session.execute(
            select(
                Episode.id,
                Episode.group_key,
                Episode.started_at,
                Episode.ended_at,
                Episode.event_count,
                Episode.status,
            ).order_by(Episode.group_key, Episode.started_at)
        ).all()
        members = session.execute(
            select(EpisodeEvent.episode_id, EpisodeEvent.event_id, EpisodeEvent.seq)
            .order_by(EpisodeEvent.episode_id, EpisodeEvent.seq)
        ).all()
        return episodes, members
    finally:
        session.close()


@pytest.fixture
def assigned():
    """The fixture corpus, imported and fully assigned at a fixed clock."""
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    import_fixture()
    assign_all(now)
    yield now


def test_fixture_produces_the_expected_partition(assigned):
    episodes, members = snapshot()
    assert len(episodes) == 14
    assert len(members) == 20


def test_a_dense_thread_stays_one_episode(assigned):
    """Five dictations inside 17 minutes are one sitting, not five."""
    session = SessionLocal()
    try:
        counts = session.execute(
            select(Episode.event_count)
            .where(Episode.group_key == "t:slack:slack:channel:pricing")
        ).scalars().all()
        assert list(counts) == [5]
    finally:
        session.close()


def test_every_event_belongs_to_exactly_one_episode(assigned):
    session = SessionLocal()
    try:
        orphans = session.scalar(
            select(func.count())
            .select_from(Event)
            .where(~Event.id.in_(select(EpisodeEvent.event_id)))
        )
        assert orphans == 0
    finally:
        session.close()


def test_sensitive_events_still_get_an_episode(assigned):
    """Nothing is excluded from grouping.

    An event outside every episode is unreachable, which breaks provenance.
    Sensitive dictations are grouped like any other; what they must not
    produce is a memory, which is enforced downstream.
    """
    session = SessionLocal()
    try:
        for external_id in ("f_005", "f_006", "f_007"):
            event = session.scalars(
                select(Event).where(Event.external_id == external_id)
            ).one()
            assert session.scalar(
                select(func.count())
                .select_from(EpisodeEvent)
                .where(EpisodeEvent.event_id == event.id)
            ) == 1
    finally:
        session.close()


def test_one_thread_splits_on_its_time_gaps(assigned):
    """slack:dm:aditya holds four events across three sittings."""
    session = SessionLocal()
    try:
        rows = session.execute(
            select(Episode.event_count)
            .where(Episode.group_key == "t:slack:slack:dm:aditya")
            .order_by(Episode.started_at)
        ).scalars().all()
        assert list(rows) == [2, 1, 1]
    finally:
        session.close()


def test_reassignment_is_byte_identical(assigned):
    """The M2 criterion: two runs agree, down to the episode ids."""
    first = snapshot()
    assign_all(assigned)
    assert snapshot() == first


def test_a_late_event_merges_two_sittings():
    """Group rebuild repairs a split that incremental append could not."""
    now = datetime(2026, 9, 8, tzinfo=timezone.utc)
    user_id = settings.default_user_id
    session = SessionLocal()
    try:
        session.execute(delete(Job).where(Job.user_id == user_id))
        session.execute(delete(Episode).where(Episode.user_id == user_id))
        session.execute(delete(Event).where(Event.user_id == user_id))
        session.commit()

        def add(minutes: int) -> None:
            session.add(
                Event(
                    user_id=user_id,
                    occurred_at=BASE + timedelta(minutes=minutes),
                    ingested_at=BASE,
                    app="slack",
                    thread_id="gap-test",
                    raw_asr="x",
                    formatted_text="x",
                    ingest_status="pending",
                )
            )
            session.commit()

        # 60 minutes apart: two sittings.
        add(0)
        add(60)
        rebuild_group(session, user_id, "t:slack:gap-test", now=now)
        session.commit()
        assert len(snapshot()[0]) == 2

        # A bridging event arrives late and closes both gaps to 30 minutes.
        add(30)
        rebuild_group(session, user_id, "t:slack:gap-test", now=now)
        session.commit()

        episodes, members = snapshot()
        assert len(episodes) == 1
        assert episodes[0].event_count == 3
        assert len(members) == 3
    finally:
        session.execute(delete(Episode).where(Episode.user_id == user_id))
        session.execute(delete(Event).where(Event.user_id == user_id))
        session.commit()
        session.close()
