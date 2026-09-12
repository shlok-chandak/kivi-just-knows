"""What is happening right now, and a way to stop waiting for it.

An episode closes on its own after twenty minutes of silence. That is the
right rule for someone dictating through a working day and the wrong one for
someone being shown the system, who would watch nothing happen for the whole
demonstration. So closing can also be asked for.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.episode import (
    IDLE_GAP,
    MAX_EVENTS_PER_EPISODE,
    MAX_SPAN,
    Episode,
)
from app.models.event import Event
from app.models.job import Job
from app.models.memory import Memory, MemoryEvidence
from app.services import queue
from app.services.episodes import assign_events, events_in_episode
from app.worker.handlers import STAGE_EPISODE_CONSOLIDATE

router = APIRouter(prefix="/episodes", tags=["episodes"])

# How many finished episodes to show behind the open one. Enough to see the
# last thing land without turning this into a history screen.
RECENT = 3


def episode_state(episode: Episode | None) -> dict | None:
    """An episode, and when it will close.

    Three things can close one and the soonest wins, so the prediction names
    which. Reported here rather than computed in the browser because the
    thresholds live in the model -- a copy in the frontend would be right
    until somebody changed one.
    """
    if episode is None:
        return None

    state: dict = {
        "id": str(episode.id),
        "status": episode.status,
        "event_count": episode.event_count,
        "started_at": episode.started_at.isoformat(),
        "ended_at": episode.ended_at.isoformat(),
        "summary_status": episode.summary_status,
        "closed_by": episode.closed_by,
        "title": episode.title,
        "summary": episode.summary,
        "topic_tags": episode.topic_tags,
        "limits": {
            "idle_minutes": IDLE_GAP.total_seconds() / 60,
            "max_span_hours": MAX_SPAN.total_seconds() / 3600,
            "max_events": MAX_EVENTS_PER_EPISODE,
        },
    }

    if episode.status == "closed":
        state["closes"] = None
        return state

    now = datetime.now(timezone.utc)
    candidates = [
        (episode.ended_at + IDLE_GAP, "nothing else said for 20 minutes"),
        (episode.started_at + MAX_SPAN, "the stretch has run two hours"),
    ]
    if episode.event_count >= MAX_EVENTS_PER_EPISODE:
        candidates.append((now, "it is full"))

    at, because = min(candidates)
    state["closes"] = {
        "at": at.isoformat(),
        "because": because,
        "in_seconds": max(int((at - now).total_seconds()), 0),
        # Each new dictation pushes the idle deadline out, so a number
        # counting down is only true until the next one lands.
        "resets_on_next": because.startswith("nothing else"),
    }
    return state


def _take(event: Event) -> dict:
    return {
        "id": str(event.id),
        "occurred_at": event.occurred_at.isoformat(),
        "app": event.app,
        "text": event.canonical_text,
        "ingest_status": event.ingest_status,
        "ignore_reason": event.ignore_reason,
    }


def _waiting(db: Session, user_id: uuid.UUID) -> list[dict]:
    """Work the queue still owes, oldest first.

    Jobs whose subject is an episode are resolved to something readable: a
    row saying `episode_consolidate 9f3a…` tells a person nothing, and this
    view exists to be read by a person.
    """
    jobs = list(
        db.scalars(
            select(Job)
            .where(Job.user_id == user_id, Job.status.in_(("pending", "running")))
            .order_by(Job.run_after, Job.created_at)
            .limit(50)
        )
    )

    spans: dict[str, Episode] = {}
    keys = [j.subject_key for j in jobs if j.subject_type == "episode"]
    if keys:
        ids = []
        for key in keys:
            try:
                ids.append(uuid.UUID(key))
            except ValueError:
                continue
        if ids:
            spans = {
                str(episode.id): episode
                for episode in db.scalars(select(Episode).where(Episode.id.in_(ids)))
            }

    now = datetime.now(timezone.utc)
    rows = []
    for job in jobs:
        episode = spans.get(job.subject_key)
        rows.append(
            {
                "id": str(job.id),
                "stage": job.stage,
                "status": job.status,
                "attempts": job.attempts,
                "run_after": job.run_after.isoformat(),
                # A job scheduled for later is waiting on the clock, not on a
                # worker. Without this the queue looks stuck when it is idle.
                "due_in_seconds": max(int((job.run_after - now).total_seconds()), 0),
                "last_error": job.last_error,
                "about": (
                    f"{episode.event_count} takes, "
                    f"{episode.started_at:%H:%M}–{episode.ended_at:%H:%M}"
                    if episode
                    else None
                ),
            }
        )
    return rows


def _finished(db: Session, user_id: uuid.UUID) -> list[dict]:
    """Closed episodes, newest first, with what was drawn out of each."""
    episodes = list(
        db.scalars(
            select(Episode)
            .where(Episode.user_id == user_id, Episode.status == "closed")
            .order_by(Episode.ended_at.desc())
            .limit(RECENT)
        )
    )
    if not episodes:
        return []

    counts = dict(
        db.execute(
            select(MemoryEvidence.episode_id, func.count(func.distinct(Memory.id)))
            .join(Memory, Memory.id == MemoryEvidence.memory_id)
            .where(MemoryEvidence.episode_id.in_([e.id for e in episodes]))
            .group_by(MemoryEvidence.episode_id)
        ).all()
    )

    rows = []
    for episode in episodes:
        state = episode_state(episode)
        assert state is not None
        state["beliefs"] = counts.get(episode.id, 0)
        # Null summary on a closed episode means consolidation has not run
        # yet, which is the state the whole panel exists to make visible.
        state["waiting_to_be_read"] = episode.summary_status is None
        rows.append(state)
    return rows


@router.get("/live")
def live(db: Session = Depends(get_db)) -> dict:
    """The open episode, what is queued behind it, and what just finished."""
    user_id = settings.default_user_id

    episode = db.scalars(
        select(Episode).where(Episode.user_id == user_id, Episode.status == "open")
    ).first()

    state = episode_state(episode)
    if state is not None and episode is not None:
        state["takes"] = [_take(e) for e in events_in_episode(db, episode.id)]

    # Dictations that arrived but have not been attached to an episode yet.
    # They belong to the open stretch in every sense except the database, and
    # hiding them would make a take look lost for as long as the queue is busy.
    unassigned = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.user_id == user_id, Event.episode_id.is_(None))
    ) or 0

    return {
        "open": state,
        "unassigned": unassigned,
        "queue": _waiting(db, user_id),
        "finished": _finished(db, user_id),
    }


def _unread(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Closed episodes with no summary, whoever left them that way.

    Assignment only reports episodes that new takes touched, so an episode
    whose summary was cleared and then missed stays invisible to it forever.
    Asking the database what is actually unread costs one indexed query and
    means a dropped hand-off repairs itself on the next click, rather than
    leaving a stretch nothing will ever read.
    """
    return list(
        db.scalars(
            select(Episode.id).where(
                Episode.user_id == user_id,
                Episode.status == "closed",
                Episode.summary_status.is_(None),
            )
        )
    )


def _stretch_of_last_take(db: Session, user_id: uuid.UUID) -> Episode | None:
    """The episode holding the most recent take.

    Used when nothing is open. A take said inside the idle window joins the
    stretch it belongs to even when a timer already closed that stretch, so
    "the one I have been talking into" is the last take's episode, not
    whichever episode happens to be open.
    """
    episode_id = db.scalar(
        select(Event.episode_id)
        .where(Event.user_id == user_id, Event.episode_id.is_not(None))
        .order_by(Event.occurred_at.desc(), Event.ingested_at.desc())
        .limit(1)
    )
    return db.get(Episode, episode_id) if episode_id else None


def _queue_consolidation(
    db: Session, user_id: uuid.UUID, episode_ids: list[uuid.UUID]
) -> int:
    """Queue the reading of every episode that needs it.

    Assignment clears the summary of any closed episode that gained takes,
    so whatever it hands back must be queued or that episode is left with no
    summary and nothing on its way to write one.
    """
    wanted = list(dict.fromkeys([*episode_ids, *_unread(db, user_id)]))
    if not wanted:
        return 0
    episode_ids = wanted
    return queue.enqueue_many(
        db,
        user_id=user_id,
        stage=STAGE_EPISODE_CONSOLIDATE,
        subject_keys=[str(episode_id) for episode_id in episode_ids],
        subject_type="episode",
    )


@router.get("/formed")
def formed(
    since: datetime,
    limit: int = 25,
    db: Session = Depends(get_db),
) -> dict:
    """What a stretch of ingestion turned into: episodes, and the beliefs each gave.

    Filtered on `created_at`, the row's own insert time, not `started_at`.
    An upload of last month's dictations produces episodes dated last month,
    so asking "what did I just import" by the time the dictation happened
    returns nothing at all.
    """
    user_id = settings.default_user_id
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)

    episodes = list(
        db.scalars(
            select(Episode)
            .where(
                Episode.user_id == user_id,
                Episode.created_at >= since,
            )
            .order_by(Episode.created_at.desc())
            .limit(limit)
        )
    )
    if not episodes:
        return {"since": since.isoformat(), "episodes": [], "beliefs": 0}

    ids = [episode.id for episode in episodes]
    evidence = list(
        db.execute(
            select(MemoryEvidence.episode_id, Memory)
            .join(Memory, Memory.id == MemoryEvidence.memory_id)
            .where(MemoryEvidence.episode_id.in_(ids))
        ).all()
    )

    beliefs: dict[uuid.UUID, list[dict]] = {}
    seen: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for episode_id, memory in evidence:
        if (episode_id, memory.id) in seen:
            continue
        seen.add((episode_id, memory.id))
        beliefs.setdefault(episode_id, []).append(
            {
                "id": str(memory.id),
                "text": memory.content,
                "type": memory.type,
                "status": memory.status,
                "confidence": memory.posterior_mean,
                "superseded": memory.superseded_by is not None,
            }
        )

    rows = []
    for episode in episodes:
        state = episode_state(episode)
        assert state is not None
        state["beliefs"] = beliefs.get(episode.id, [])
        state["takes"] = [
            {
                "id": str(event.id),
                "text": event.canonical_text,
                "app": event.app,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in events_in_episode(db, episode.id)
        ]
        rows.append(state)

    return {
        "since": since.isoformat(),
        "episodes": rows,
        "beliefs": sum(len(row["beliefs"]) for row in rows),
    }


@router.post("/close-open")
def close_open(db: Session = Depends(get_db)) -> dict:
    """Close whatever is open, without naming it.

    The id-taking route below is fine for a script that already knows which
    episode it means. A button does not: the worker can attach new takes and
    open a different episode between the page reading an id and a person
    clicking, and then the click would close the wrong stretch. Asking for
    "the open one" after assignment has run cannot go stale that way.
    """
    user_id = settings.default_user_id
    result = assign_events(db, user_id=user_id)
    pending = list(result.get("needs_consolidation", []))
    db.flush()

    episode = db.scalars(
        select(Episode).where(Episode.user_id == user_id, Episode.status == "open")
    ).first()

    if episode is None:
        # Nothing open, because the takes just said joined a stretch a time
        # rule had already closed. The intent is the same either way -- that
        # stretch is finished -- so seal it, which is what stops the next
        # dictation being swallowed by it too.
        episode = _stretch_of_last_take(db, user_id)

    sealed = False
    if episode is not None and episode.closed_by != "hand":
        episode.status = "closed"
        episode.closed_by = "hand"
        sealed = True
        if episode.summary_status is None and episode.id not in pending:
            pending.append(episode.id)
        db.flush()

    queued = _queue_consolidation(db, user_id, pending)
    db.commit()
    if episode is not None:
        db.refresh(episode)

    return {
        "closed": sealed,
        "reason": None if sealed else "nothing left to close",
        "assigned": result.get("assigned", 0),
        "queued": queued,
        "episode": episode_state(episode),
    }


@router.post("/{episode_id}/close")
def close(episode_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    """Close an episode now, rather than waiting for it to go quiet.

    Assignment runs first, deliberately. A dictation sent a moment ago may
    not be attached to an episode yet, and closing before it lands would
    leave it to start a new one -- so the button would appear to lose the
    last thing said, which is the opposite of what it is for.
    """
    user_id = settings.default_user_id

    result = assign_events(db, user_id=user_id)
    pending = list(result.get("needs_consolidation", []))

    episode = db.get(Episode, episode_id)
    if episode is None or episode.user_id != user_id:
        raise HTTPException(status_code=404, detail="episode not found")

    forced = False
    if episode.status == "open":
        episode.status = "closed"
        episode.closed_by = "hand"
        forced = True
        if episode.summary_status is None and episode.id not in pending:
            pending.append(episode.id)

    db.flush()

    queued = _queue_consolidation(db, user_id, pending)
    db.commit()
    db.refresh(episode)

    return {
        "closed": forced,
        "assigned": result.get("assigned", 0),
        "queued": queued,
        "episode": episode_state(episode),
    }
