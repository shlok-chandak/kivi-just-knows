"""The always-on profile: what it holds, and how rarely it changes.

Two properties matter. An edit survives a rebuild, because a profile that
overwrites the person's corrections is not editable. And a rebuild happens
at most once a day, because this is the one thing sent with every question
and it should be stable enough to read.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.models.job import Job
from app.models.memory import Memory
from app.models.profile import ProfileEntry
from app.services import profile, queue
from tests.conftest import TEST_USER

USER = TEST_USER
NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    from app.db.session import SessionLocal

    session = SessionLocal()
    _clear(session)
    yield session
    _clear(session)
    session.close()


def _clear(session) -> None:
    session.execute(delete(ProfileEntry).where(ProfileEntry.user_id == USER))
    session.execute(delete(Memory).where(Memory.user_id == USER))
    session.execute(delete(Job).where(Job.user_id == USER))
    session.commit()


def preference(session, content: str, *, days_ago: int = 1) -> Memory:
    memory = Memory(
        id=uuid.uuid4(),
        user_id=USER,
        type="preference",
        content=content,
        claim_key=uuid.uuid4().hex[:32],
        status="active",
        alpha=2.0,
        beta=1.0,
        observation_count=1,
        last_reinforced_at=NOW - timedelta(days=days_ago),
    )
    session.add(memory)
    session.flush()
    return memory


def test_preferences_become_resident(db):
    preference(db, "Release notes as short bullets, never paragraphs.")
    built = profile.refresh(db, USER, now=NOW)

    assert [entry.memory.content for entry in built.style] == [
        "Release notes as short bullets, never paragraphs."
    ]


def test_a_hidden_entry_stays_hidden_through_a_rebuild(db):
    """Otherwise an edit lasts until the next thing the person dictates."""
    memory = preference(db, "Meetings before noon.")
    profile.refresh(db, USER, now=NOW)

    profile.set_state(db, USER, memory.id, "hidden")
    assert profile.load(db, USER, now=NOW).style == []

    profile.refresh(db, USER, now=NOW)
    assert profile.load(db, USER, now=NOW).style == []


def test_a_pinned_entry_survives_being_superseded(db):
    """The person asked for it; the ranking does not get to overrule that."""
    memory = preference(db, "Trellis get full sentences.")
    profile.set_state(db, USER, memory.id, "pinned")

    memory.status = "superseded"
    db.flush()
    profile.refresh(db, USER, now=NOW)

    assert len(profile.load(db, USER, now=NOW).style) == 1


def test_superseded_memories_are_not_resident(db):
    """A replaced belief repeated in every prompt is worse than none."""
    memory = preference(db, "Always include the rupee figure.")
    memory.status = "superseded"
    db.flush()

    assert profile.refresh(db, USER, now=NOW).style == []


def test_work_is_capped_so_it_cannot_evict_style(db):
    for index in range(profile.MAX_WORK_LINES + 4):
        db.add(
            Memory(
                id=uuid.uuid4(),
                user_id=USER,
                type="decision",
                content=f"Decision number {index}.",
                claim_key=uuid.uuid4().hex[:32],
                status="active",
                alpha=2.0,
                beta=1.0,
                observation_count=1,
                last_reinforced_at=NOW - timedelta(days=index),
            )
        )
    preference(db, "Meetings before noon.")
    db.flush()

    built = profile.refresh(db, USER, now=NOW)
    assert len(built.work) == profile.MAX_WORK_LINES
    assert len(built.style) == 1


def test_work_is_ordered_newest_first(db):
    """Durability ranking put a two-month-old decision under 'currently'."""
    for index, days in enumerate([40, 2, 20]):
        db.add(
            Memory(
                id=uuid.uuid4(),
                user_id=USER,
                type="decision",
                content=f"Decision {days} days old.",
                claim_key=uuid.uuid4().hex[:32],
                status="active",
                alpha=2.0,
                beta=1.0,
                observation_count=1,
                last_reinforced_at=NOW - timedelta(days=days),
            )
        )
    db.flush()

    built = profile.refresh(db, USER, now=NOW)
    assert [entry.memory.content for entry in built.work] == [
        "Decision 2 days old.",
        "Decision 20 days old.",
        "Decision 40 days old.",
    ]


def test_a_rebuild_is_not_due_again_for_a_day(db):
    """The throttle, which is the only thing keeping this stable to read."""
    db.add(
        Job(
            id=uuid.uuid4(),
            user_id=USER,
            stage=profile.REFRESH_STAGE,
            subject_type="group",
            subject_key=profile.REFRESH_SUBJECT,
            status="done",
            attempts=1,
            finished_at=NOW,
        )
    )
    db.flush()

    due = profile.next_refresh_due(db, USER, now=NOW)
    assert due == NOW + profile.REFRESH_INTERVAL


def test_the_first_rebuild_is_due_immediately(db):
    assert profile.next_refresh_due(db, USER, now=NOW) == NOW


def test_a_scheduled_rebuild_blocks_another_being_queued(db):
    """Everything said before it is due collapses into the one rebuild."""
    later = NOW + timedelta(days=1)
    assert queue.enqueue(
        db, user_id=USER, stage=profile.REFRESH_STAGE,
        subject_key=profile.REFRESH_SUBJECT, run_after=later,
    ) is True
    db.commit()

    assert queue.enqueue(
        db, user_id=USER, stage=profile.REFRESH_STAGE,
        subject_key=profile.REFRESH_SUBJECT, run_after=later,
    ) is False
    db.commit()


# --- filtering when there are more preferences than fit ----------------------


def _preference(session, content: str, *, days_ago: int, uses: int = 0) -> Memory:
    memory = preference(session, content, days_ago=days_ago)
    memory.use_count = uses
    session.flush()
    return memory


def test_everything_fits_when_under_the_cap(db):
    for index in range(MAX := profile.MAX_STYLE_LINES):
        _preference(db, f"Preference {index}.", days_ago=index)

    assert len(profile.refresh(db, USER, now=NOW).style) == MAX


def test_over_the_cap_the_oldest_are_dropped_first(db):
    """Recency leads, because it is the signal that actually varies."""
    for index in range(profile.MAX_STYLE_LINES + 6):
        _preference(db, f"Preference {index}.", days_ago=index)

    kept = {entry.memory.content for entry in profile.refresh(db, USER, now=NOW).style}
    assert len(kept) == profile.MAX_STYLE_LINES
    # The newest is in and the oldest is out.
    assert "Preference 0." in kept
    assert "Preference 15." not in kept


def test_a_proven_useful_older_preference_beats_a_trivial_newer_one(db):
    """Why style is ranked rather than cut off by date.

    The oldest real preference in the corpus was also the only one ever cited
    in an answer. Dropping the oldest first would have retired exactly the
    one that had earned its place.
    """
    for index in range(profile.MAX_STYLE_LINES):
        _preference(db, f"Never used {index}.", days_ago=index + 1)

    veteran = _preference(db, "Release notes as short bullets.", days_ago=60, uses=8)
    veteran.alpha = 6.0
    db.flush()

    kept = {entry.memory.content for entry in profile.refresh(db, USER, now=NOW).style}
    assert "Release notes as short bullets." in kept


def test_a_preference_is_still_resident_a_year_on(db):
    """Preferences are close to permanent: a year is not old for one."""
    _preference(db, "Meetings before noon.", days_ago=365)

    kept = [e.memory.content for e in profile.refresh(db, USER, now=NOW).style]
    assert kept == ["Meetings before noon."]


def test_decay_eventually_retires_a_preference_nobody_restates(db):
    """Without a cliff. Age loses to fresher preferences rather than to a rule."""
    ancient = _preference(db, "Ancient but once useful.", days_ago=1200, uses=50)
    ancient.alpha = 20.0
    for index in range(profile.MAX_STYLE_LINES):
        _preference(db, f"Recent {index}.", days_ago=index + 1)
    db.flush()

    kept = {entry.memory.content for entry in profile.refresh(db, USER, now=NOW).style}
    assert "Ancient but once useful." not in kept


def test_work_is_strictly_newest_first_however_useful_the_old_one_was(db):
    """The difference from style: currently means currently."""
    old = Memory(
        id=uuid.uuid4(), user_id=USER, type="decision",
        content="An old decision everyone kept citing.",
        claim_key=uuid.uuid4().hex[:32], status="active",
        alpha=20.0, beta=1.0, observation_count=9, use_count=50,
        last_reinforced_at=NOW - timedelta(days=120),
    )
    db.add(old)
    for index in range(profile.MAX_WORK_LINES):
        db.add(
            Memory(
                id=uuid.uuid4(), user_id=USER, type="decision",
                content=f"Decision {index}.", claim_key=uuid.uuid4().hex[:32],
                status="active", alpha=2.0, beta=1.0, observation_count=1,
                last_reinforced_at=NOW - timedelta(days=index + 1),
            )
        )
    db.flush()

    kept = {entry.memory.content for entry in profile.refresh(db, USER, now=NOW).work}
    assert "An old decision everyone kept citing." not in kept


def test_a_refresh_queues_the_next_one_a_day_out(db):
    """The profile keeps up on its own, or it only works when poked.

    Currency decays with the clock, so what belongs in the profile changes
    even on a day nobody dictated anything -- which means the rebuild cannot
    depend on consolidation noticing a belief moved.
    """
    from app.models.job import Job as JobModel
    from app.worker.handlers import handle_profile_refresh

    session = db
    preference(session, "Release notes as short bullets.")
    job = JobModel(
        user_id=USER,
        stage=profile.REFRESH_STAGE,
        subject_type="group",
        subject_key=profile.REFRESH_SUBJECT,
        status="running",
        attempts=1,
    )
    session.add(job)
    session.flush()

    handle_profile_refresh(session, job)
    session.commit()

    queued = list(
        session.scalars(
            select(JobModel).where(
                JobModel.user_id == USER,
                JobModel.stage == profile.REFRESH_STAGE,
                JobModel.status == "pending",
            )
        )
    )

    assert len(queued) == 1, "the next rebuild should already be waiting"
    waited = queued[0].run_after - datetime.now(timezone.utc)
    assert timedelta(hours=23) < waited <= profile.REFRESH_INTERVAL
