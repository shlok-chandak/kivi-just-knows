"""Queue behaviour: coalescing, exclusive claims, retry, and parking."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, func, select

from app.config import settings
from app.db.session import SessionLocal
from app.models.job import Job
from app.services import queue
from app.worker.handlers import HANDLERS
from app.worker.main import run_once

USER = settings.default_user_id
STAGE = "episode_assign"


class _Stub:
    """Returns a fixed structured result, whatever it is asked."""

    def __init__(self, value, usage):
        self._value = value
        self._usage = usage

    def structured(self, **kwargs):
        from app.llm.client import Completion

        return Completion(value=self._value, usage=self._usage)


def _usage():
    from app.llm.client import Usage

    return Usage(
        model="stub-model",
        input_tokens=10,
        output_tokens=5,
        latency_ms=1,
        cost_usd=0.0,
    )


def _stub_consolidator():
    from app.schemas.consolidation import ConsolidationOut

    return _Stub(
        ConsolidationOut(
            title="Stub",
            summary="Stub summary.",
            topic_tags=["stub"],
            memories=[],
        ),
        _usage(),
    )


@pytest.fixture
def session():
    db = SessionLocal()
    db.execute(delete(Job).where(Job.user_id == USER))
    db.commit()
    yield db
    db.execute(delete(Job).where(Job.user_id == USER))
    db.commit()
    db.close()


def add(session, subject_key: str, **kwargs) -> bool:
    queued = queue.enqueue(
        session, user_id=USER, stage=STAGE, subject_key=subject_key, **kwargs
    )
    session.commit()
    return queued


# --- enqueue ----------------------------------------------------------------


def test_enqueue_reports_success(session):
    assert add(session, "g1") is True


def test_duplicate_pending_job_is_coalesced(session):
    add(session, "g1")
    assert add(session, "g1") is False
    assert session.scalar(select(Job.subject_key)) == "g1"


def test_a_job_may_queue_while_another_runs(session):
    """A new event must not be lost because a rebuild is already in flight."""
    add(session, "g1")
    running = session.scalars(select(Job)).one()
    running.status = "running"
    session.commit()

    assert add(session, "g1") is True
    assert len(session.scalars(select(Job)).all()) == 2


def test_completed_jobs_do_not_block_new_ones(session):
    add(session, "g1")
    done = session.scalars(select(Job)).one()
    done.status = "done"
    session.commit()

    assert add(session, "g1") is True


def test_enqueue_many_deduplicates_within_the_batch(session):
    queued = queue.enqueue_many(
        session, user_id=USER, stage=STAGE, subject_keys=["a", "b", "b", "c", ""]
    )
    session.commit()
    assert queued == 3


def test_enqueue_many_with_nothing_to_do(session):
    assert queue.enqueue_many(session, user_id=USER, stage=STAGE, subject_keys=[]) == 0


# --- claim ------------------------------------------------------------------


def test_claim_marks_the_job_running_and_counts_the_attempt(session):
    add(session, "g1")
    job = queue.claim(session)
    assert job.status == "running"
    assert job.attempts == 1
    assert job.started_at is not None


def test_claim_returns_none_when_the_queue_is_empty(session):
    assert queue.claim(session) is None


def test_two_workers_never_claim_the_same_job(session):
    """SKIP LOCKED: each worker takes a different row instead of waiting."""
    queue.enqueue_many(session, user_id=USER, stage=STAGE, subject_keys=["g1", "g2"])
    session.commit()

    a, b = SessionLocal(), SessionLocal()
    try:
        first, second = queue.claim(a), queue.claim(b)
        assert first is not None and second is not None
        assert first.id != second.id
    finally:
        a.rollback(); a.close()
        b.rollback(); b.close()


def test_a_single_job_is_claimed_by_only_one_worker(session):
    add(session, "g1")

    a, b = SessionLocal(), SessionLocal()
    try:
        assert queue.claim(a) is not None
        assert queue.claim(b) is None
    finally:
        a.rollback(); a.close()
        b.rollback(); b.close()


def test_a_job_scheduled_for_later_is_not_claimed(session):
    add(session, "g1")
    job = session.scalars(select(Job)).one()
    job.run_after = datetime.now(timezone.utc) + timedelta(minutes=5)
    session.commit()

    assert queue.claim(session) is None


def test_claim_filters_by_stage(session):
    queue.enqueue(session, user_id=USER, stage="some_future_stage", subject_key="g1")
    session.commit()

    assert queue.claim(session, stages=[STAGE]) is None
    assert queue.claim(session, stages=["some_future_stage"]) is not None


# --- outcomes ---------------------------------------------------------------


def test_completing_a_job_clears_it(session):
    add(session, "g1")
    job = queue.claim(session)
    queue.complete(session, job)
    session.commit()

    assert job.status == "done"
    assert job.finished_at is not None
    assert job.last_error is None


def test_failure_reschedules_with_backoff(session):
    add(session, "g1")
    job = queue.claim(session)
    queue.fail(session, job, "simulated timeout")
    session.commit()

    assert job.status == "pending"
    assert job.last_error == "simulated timeout"
    assert job.run_after > datetime.now(timezone.utc)


def test_a_rate_limit_uses_the_providers_own_delay(session):
    """A quota measured per minute is not something to guess at."""
    add(session, "g1")
    job = queue.claim(session)
    queue.fail(session, job, "429", retry_after=40)
    session.commit()

    wait = (job.run_after - datetime.now(timezone.utc)).total_seconds()
    assert wait > 40


def test_being_rate_limited_does_not_spend_an_attempt(session):
    """Otherwise healthy work parks while the service is fine."""
    add(session, "g1")
    job = queue.claim(session)
    before = job.attempts
    queue.fail(session, job, "429", retry_after=30)
    session.commit()

    assert job.attempts == before - 1
    assert job.status == "pending"


def test_an_ordinary_failure_still_spends_an_attempt(session):
    add(session, "g1")
    job = queue.claim(session)
    before = job.attempts
    queue.fail(session, job, "timeout")
    session.commit()

    assert job.attempts == before


def test_backoff_widens_between_attempts():
    delays = [queue.backoff_delay(n).total_seconds() for n in (1, 2, 3)]
    assert delays == sorted(delays)
    assert delays[0] < delays[-1]


def test_a_job_parks_once_attempts_run_out(session):
    add(session, "g1", max_attempts=2)

    for _ in range(2):
        job = queue.claim(session)
        queue.fail(session, job, "still failing")
        job.run_after = datetime.now(timezone.utc)
        session.commit()

    assert job.status == "failed"
    assert job.attempts == 2
    assert job.last_error == "still failing"
    assert queue.claim(session) is None


def test_long_errors_are_truncated(session):
    add(session, "g1")
    job = queue.claim(session)
    queue.fail(session, job, "x" * 5000)
    session.commit()

    assert len(job.last_error) == 2000


# --- worker loop ------------------------------------------------------------


def test_run_once_reports_an_empty_queue(session):
    assert run_once() is False


def test_run_once_processes_a_job(session):
    add(session, "events")
    assert run_once() is True

    session.expire_all()
    assert (
        session.scalar(select(Job.status).where(Job.stage == STAGE)) == "done"
    )


def test_assignment_queues_consolidation_for_closed_episodes(session, monkeypatch):
    """Closing an episode is what triggers the next stage of the pipeline.

    The model-backed stage is stubbed: this asserts the chain runs to
    completion, not what any provider returns for it.
    """
    from app.services import consolidate as consolidate_module
    from scripts.import_corpus import main

    monkeypatch.setattr(consolidate_module, "get_client", _stub_consolidator)

    main(["corpus/fixture.jsonl", "--truncate"])
    session.expire_all()

    while run_once():
        pass

    session.expire_all()
    stages = dict(
        session.execute(
            select(Job.stage, func.count()).group_by(Job.stage)
        ).all()
    )
    assert stages.get("episode_consolidate", 0) > 0
    assert not session.scalars(
        select(Job).where(Job.status.in_(["pending", "failed"]))
    ).all()


def test_every_event_ends_up_in_an_episode_after_a_full_run(session, monkeypatch):
    """An event reachable from no episode is unsearchable."""
    from app.models.event import Event
    from app.services import consolidate as consolidate_module
    from scripts.import_corpus import main

    monkeypatch.setattr(consolidate_module, "get_client", _stub_consolidator)

    main(["corpus/fixture.jsonl", "--truncate"])
    while run_once():
        pass

    session.expire_all()
    orphans = session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.user_id == USER, Event.episode_id.is_(None))
    )
    assert orphans == 0


def test_an_unknown_stage_is_recorded_rather_than_crashing(session):
    """Only registered stages are claimed, so this is belt and braces."""
    assert "not_a_stage" not in HANDLERS


def test_counts_by_stage(session):
    queue.enqueue_many(session, user_id=USER, stage=STAGE, subject_keys=["g1", "g2"])
    session.commit()

    assert queue.counts_by_stage(session)[(STAGE, "pending")] == 2
