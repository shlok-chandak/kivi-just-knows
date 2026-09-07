"""Durable work queue over the `jobs` table.

Claiming uses FOR UPDATE SKIP LOCKED, so concurrent workers take different
rows instead of contending for the same one.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.job import Job

# Backoff between retries: 5s, 20s, 80s.
BACKOFF_BASE_SECONDS = 5
BACKOFF_FACTOR = 4


def backoff_delay(attempts: int) -> timedelta:
    return timedelta(seconds=BACKOFF_BASE_SECONDS * (BACKOFF_FACTOR ** max(attempts - 1, 0)))


def enqueue(
    session: Session,
    *,
    user_id: uuid.UUID,
    stage: str,
    subject_key: str,
    subject_type: str = "group",
    max_attempts: int | None = None,
) -> bool:
    """Queue one job. Returns False when an identical job is already waiting."""
    inserted = enqueue_many(
        session,
        user_id=user_id,
        stage=stage,
        subject_keys=[subject_key],
        subject_type=subject_type,
        max_attempts=max_attempts,
    )
    return inserted == 1


def enqueue_many(
    session: Session,
    *,
    user_id: uuid.UUID,
    stage: str,
    subject_keys: list[str],
    subject_type: str = "group",
    max_attempts: int | None = None,
) -> int:
    """Queue many jobs in one statement, skipping any already waiting.

    Deduplicates within the batch too: a bulk import touches the same thread
    many times, and one rebuild covers all of it.
    """
    unique_keys = list(dict.fromkeys(key for key in subject_keys if key))
    if not unique_keys:
        return 0

    rows: list[dict[str, Any]] = [
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "stage": stage,
            "subject_type": subject_type,
            "subject_key": key,
            "status": "pending",
            "attempts": 0,
            **({"max_attempts": max_attempts} if max_attempts else {}),
        }
        for key in unique_keys
    ]

    statement = (
        insert(Job)
        .values(rows)
        .on_conflict_do_nothing(
            index_elements=["stage", "subject_key"],
            index_where=text("status = 'pending'"),
        )
        # RETURNING rather than rowcount: rowcount is unreliable for
        # ON CONFLICT DO NOTHING and reports -1 on this driver.
        .returning(Job.id)
    )
    return len(session.execute(statement).scalars().all())


def claim(session: Session, stages: list[str] | None = None) -> Job | None:
    """Take the oldest due job, marking it running. None if nothing is due.

    Must be called inside a transaction: the row stays locked until commit.
    """
    query = (
        select(Job)
        .where(Job.status == "pending", Job.run_after <= func.now())
        .order_by(Job.run_after, Job.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if stages:
        query = query.where(Job.stage.in_(stages))

    job = session.scalars(query).first()
    if job is None:
        return None

    job.status = "running"
    job.attempts += 1
    job.started_at = datetime.now(timezone.utc)
    job.last_error = None
    session.flush()
    return job


def complete(session: Session, job: Job) -> None:
    job.status = "done"
    job.finished_at = datetime.now(timezone.utc)
    job.last_error = None
    session.flush()


def fail(session: Session, job: Job, error: str) -> None:
    """Reschedule with backoff, or park the job once attempts run out."""
    job.last_error = error[:2000]

    if job.attempts >= job.max_attempts:
        job.status = "failed"
        job.finished_at = datetime.now(timezone.utc)
    else:
        job.status = "pending"
        job.run_after = datetime.now(timezone.utc) + backoff_delay(job.attempts)

    session.flush()


def counts_by_stage(session: Session) -> dict[tuple[str, str], int]:
    """Queue depth per (stage, status). Feeds ingest progress reporting."""
    rows = session.execute(
        select(Job.stage, Job.status, func.count()).group_by(Job.stage, Job.status)
    ).all()
    return {(stage, status): count for stage, status, count in rows}
