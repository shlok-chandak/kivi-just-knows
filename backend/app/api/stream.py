"""Watching the pipeline decide, one dictation at a time.

A progress bar says how far along a run is. This says what it is doing:
which dictation was stored, which was refused and under what rule, which
episode produced beliefs and which produced none. The eliminations are the
part worth watching -- a memory system is judged as much by what it declines
as by what it keeps, and that argument is far stronger seen live than read
afterwards in a log.

The worker runs in another process, so nothing here hooks into it. The
stream watches the same rows the worker writes, carrying a watermark per
kind so each row is sent once. That has a useful property: a viewer who
connects halfway through sees everything from that point, and one who
reconnects does not replay what they already saw.

Database work runs in a thread, so a poll never blocks the event loop.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool

from app.api.ask import reference_now
from app.config import settings
from app.db.session import SessionLocal
from app.models.episode import Episode
from app.models.event import Event
from app.models.job import Job
from app.models.memory import Memory
from app.models.rejected import RejectedCandidate
from app.models.trace import Trace, TraceStep
from app.services import asking

router = APIRouter(prefix="/stream", tags=["stream"])

# Rules where the candidate is the thing being refused. Same withholding as
# the ignore log: showing it here would undo the refusal just as surely.
WITHHELD = ("sensitive_category", "sensitive_on_review")

_SSE = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Nginx and friends buffer streams into uselessness otherwise.
    "X-Accel-Buffering": "no",
}

POLL_SECONDS = 0.6
# A browser tab left open should not hold a connection forever.
MAX_SECONDS = 1800
# How many consecutive idle polls before an `until_idle` stream closes. More
# than one, because the gap between two jobs is not the end of the run.
IDLE_POLLS = 5


class _Watermark:
    """What has already been sent, so nothing is sent twice.

    Starts at the moment of connection. Watching an upload means watching
    what happens next, and replaying a corpus that was ingested last week
    buries it.
    """

    def __init__(self, since: datetime | None) -> None:
        self.event = since
        self.ignored = since
        self.step = since


def _frame(kind: str, payload: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"


# What each stage is doing, said the way a person would say it.
PHASES = {
    "embed": "Embedding dictations",
    "episode_assign": "Grouping dictations into episodes",
    "episode_consolidate": "Reading an episode and extracting beliefs",
    "profile_refresh": "Rebuilding the profile",
}

# Pipeline order, so with several stages due the reported phase is the one
# actually being worked through rather than whichever happens to be biggest.
_ORDER = {"embed": 0, "episode_assign": 1, "episode_consolidate": 2,
          "profile_refresh": 3}


def _status(session, user_id: uuid.UUID) -> dict[str, Any]:
    """What is happening right now, not what has already happened.

    The feed only speaks when something finishes, which leaves a silent gap
    for however long a model call takes. This fills it: the stage in flight,
    how long it has been going, and how much is behind it.
    """
    now = datetime.now(timezone.utc)
    running = [
        {
            "stage": stage,
            "subject": subject,
            "seconds": round((now - started).total_seconds(), 1) if started else None,
        }
        for stage, subject, started in session.execute(
            select(Job.stage, Job.subject_key, Job.started_at)
            .where(Job.user_id == user_id, Job.status == "running")
            .order_by(Job.started_at)
        )
    ]
    # Due now, not merely pending. The daily profile rebuild sits in the
    # queue all day with a run_after in the future; counting it as waiting
    # work would report the system as busy while it is asleep.
    pending = {
        stage: total
        for stage, total in session.execute(
            select(Job.stage, func.count())
            .where(
                Job.user_id == user_id,
                Job.status == "pending",
                Job.run_after <= func.now(),
            )
            .group_by(Job.stage)
        )
    }
    waiting = sum(pending.values())

    scheduled = session.scalar(
        select(func.count()).select_from(Job).where(
            Job.user_id == user_id,
            Job.status == "pending",
            Job.run_after > func.now(),
        )
    ) or 0

    # `running` is almost always empty, and that is not a bug. The worker
    # holds one transaction per job, so the row it marked running stays
    # uncommitted until the job finishes -- from any other connection the
    # job goes straight from pending to done. Work in flight is therefore
    # inferred from what is due rather than read directly.
    if running:
        first = running[0]
        phase = PHASES.get(first["stage"], first["stage"].replace("_", " "))
        detail = f"{waiting} behind it" if waiting else "last one"
    elif waiting:
        stage = min(pending, key=lambda s: _ORDER.get(s, 99))
        phase = PHASES.get(stage, stage.replace("_", " "))
        detail = f"{waiting} in the queue"
    else:
        phase = "Idle"
        detail = (
            f"nothing due; {scheduled} scheduled for later"
            if scheduled
            else "nothing in the queue"
        )

    return {
        "phase": phase,
        "detail": detail,
        "running": running,
        "pending_by_stage": pending,
        "waiting": waiting,
        "scheduled_later": scheduled,
        "idle": not running and not waiting,
    }


def _counters(session, user_id: uuid.UUID) -> dict[str, Any]:
    def count(model) -> int:
        return session.scalar(
            select(func.count()).select_from(model).where(model.user_id == user_id)
        ) or 0

    queue = {
        status: total
        for status, total in session.execute(
            select(Job.status, func.count())
            .where(Job.user_id == user_id)
            .group_by(Job.status)
        )
    }
    ignored = {
        rule: total
        for rule, total in session.execute(
            select(RejectedCandidate.rejection_rule, func.count())
            .where(RejectedCandidate.user_id == user_id)
            .group_by(RejectedCandidate.rejection_rule)
        )
    }
    cost = session.scalar(
        select(func.coalesce(func.sum(Trace.total_cost_usd), 0.0)).where(
            Trace.user_id == user_id
        )
    )
    return {
        "events": count(Event),
        "episodes": count(Episode),
        "memories": count(Memory),
        "ignored": ignored,
        "ignored_total": sum(ignored.values()),
        "queue": queue,
        "pending": queue.get("pending", 0) + queue.get("running", 0),
        "cost_usd": round(float(cost or 0.0), 5),
        "status": _status(session, user_id),
    }


def _is_uuid(value: str) -> bool:
    """Not every stage keys on an episode -- the profile rebuild keys on a word."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def _poll(user_id: uuid.UUID, mark: _Watermark) -> tuple[list[str], bool]:
    """Everything new since the last poll, and whether the queue is idle."""
    frames: list[str] = []
    with SessionLocal() as session:
        # Dictations that were kept.
        query = select(Event).where(Event.user_id == user_id)
        if mark.event:
            query = query.where(Event.created_at > mark.event)
        for row in session.scalars(query.order_by(Event.created_at).limit(50)):
            mark.event = row.created_at
            frames.append(_frame("event", {
                "id": str(row.id),
                "occurred_at": row.occurred_at.isoformat(),
                "app": row.app,
                "text": (row.canonical_text or "")[:200],
                "status": row.ingest_status,
                # Stored, but deliberately left out of the index. Without the
                # reason the feed can only say "kept", which is true and
                # misleading in the same breath.
                "ignore_reason": row.ignore_reason,
            }))

        # Dictations and claims that were not.
        query = select(RejectedCandidate).where(RejectedCandidate.user_id == user_id)
        if mark.ignored:
            query = query.where(RejectedCandidate.at > mark.ignored)
        for row in session.scalars(query.order_by(RejectedCandidate.at).limit(50)):
            mark.ignored = row.at
            withheld = row.rejection_rule in WITHHELD
            frames.append(_frame("ignored", {
                "id": str(row.id),
                "rule": row.rejection_rule,
                "rationale": row.rationale,
                "app": row.app,
                "candidate": None if withheld else row.candidate,
                "withheld": withheld,
                "at": row.at.isoformat() if row.at else None,
            }))

        # Episodes that reached a model, and what they produced.
        query = (
            select(TraceStep, Trace)
            .join(Trace, Trace.id == TraceStep.trace_id)
            .where(Trace.user_id == user_id, Trace.kind == "ingest")
        )
        if mark.step:
            query = query.where(TraceStep.created_at > mark.step)
        steps = list(session.execute(query.order_by(TraceStep.created_at).limit(50)))

        # The title and summary the model wrote for each episode. They are
        # the readable half of what consolidation produced, and reporting
        # that it ran without them says a stage happened, not what it made.
        subjects = {trace.subject_key for _, trace in steps if trace.subject_key}
        episodes: dict[str, Episode] = {}
        if subjects:
            ids = [uuid.UUID(key) for key in subjects if _is_uuid(key)]
            if ids:
                episodes = {
                    str(episode.id): episode
                    for episode in session.scalars(
                        select(Episode).where(
                            Episode.id.in_(ids),
                            Episode.user_id == user_id,
                        )
                    )
                }

        for step, trace in steps:
            mark.step = step.created_at
            out = step.output_summary or {}
            episode = episodes.get(trace.subject_key or "")
            frames.append(_frame("stage", {
                "stage": step.stage,
                "episode_id": trace.subject_key,
                "title": episode.title if episode else None,
                "summary": episode.summary if episode else None,
                "topic_tags": (episode.topic_tags or []) if episode else [],
                "decision": step.decision,
                "rationale": step.rationale,
                "dictations": (step.input_summary or {}).get("event_count"),
                "created": out.get("created"),
                "reinforced": out.get("reinforced"),
                "refused": out.get("refused"),
                "claims": out.get("claims") or [],
                "latency_ms": step.latency_ms,
                "cost_usd": round(float(step.cost_usd), 6) if step.cost_usd else None,
                "at": step.created_at.isoformat(),
            }))

        counters = _counters(session, user_id)

    frames.append(_frame("progress", counters))
    return frames, counters["pending"] == 0


@router.get("/ingest")
async def ingest(
    until_idle: bool = Query(
        default=False,
        description="close the stream once the queue has been empty for a few polls",
    ),
    replay: bool = Query(
        default=False,
        description="send everything already ingested before following new work",
    ),
) -> StreamingResponse:
    """Server-Sent Events describing the pipeline as it runs."""
    user_id = settings.default_user_id

    async def frames() -> AsyncIterator[str]:
        started = datetime.now(timezone.utc)
        mark = _Watermark(None if replay else started)
        idle_for = 0

        # Tell the client what it is joining, so a viewer that connects
        # mid-run starts from real numbers rather than from zero.
        with SessionLocal() as session:
            yield _frame("hello", {
                "at": started.isoformat(),
                **_counters(session, user_id),
            })

        while True:
            batch, idle = await run_in_threadpool(_poll, user_id, mark)
            for frame in batch:
                yield frame

            idle_for = idle_for + 1 if idle else 0
            if until_idle and idle_for >= IDLE_POLLS:
                yield _frame("done", {"reason": "queue idle"})
                return
            if (datetime.now(timezone.utc) - started).total_seconds() > MAX_SECONDS:
                yield _frame("done", {"reason": "stream time limit"})
                return

            await asyncio.sleep(POLL_SECONDS)

    return StreamingResponse(frames(), media_type="text/event-stream", headers=_SSE)


# --- watching one question being answered -----------------------------------

# How long to wait on the queue before saying something anyway. A model call
# is several seconds of silence, and a stream that goes quiet is
# indistinguishable from one that has died.
HEARTBEAT_SECONDS = 2.0


def _step_frame(step: TraceStep) -> dict[str, Any]:
    """One stage, as it happened.

    Richer than the stored trace summary on purpose: this is read once,
    live, by someone watching the reasoning, where the table is read back
    later by someone diagnosing it.
    """
    return {
        "seq": step.seq,
        "stage": step.stage,
        "decision": step.decision,
        "rationale": step.rationale,
        "input": step.input_summary,
        "output": step.output_summary,
        "latency_ms": step.latency_ms,
        "model": step.model,
        "tokens": (
            {"input": step.input_tokens, "output": step.output_tokens}
            if step.model
            else None
        ),
        "cost_usd": round(float(step.cost_usd), 6) if step.cost_usd else None,
    }


@router.get("/ask")
async def ask(
    request: str = Query(min_length=1, max_length=2000),
    text: str | None = Query(default=None),
    now: str | None = Query(
        default=None,
        description="reference date for 'yesterday' and the like, ISO 8601",
    ),
    x_kivi_now: str | None = Header(default=None),
) -> StreamingResponse:
    """Answer a question, reporting each stage as it finishes.

    The same call as POST /ask and the same code path -- the difference is
    only that the stages are handed out as they complete rather than at the
    end. Nothing in the pipeline knows it is being watched: every stage
    already announces itself to the trace recorder, and this listens in.

    The clock can arrive as a query parameter as well as a header, which
    POST /ask does not need. A browser opens this with EventSource, and
    EventSource cannot send headers -- so without the parameter a page can
    only ever ask against today, and re-asking a recorded question means
    resolving "yesterday" against a different day than the recording did.
    """
    user_id = settings.default_user_id
    reference = reference_now(now or x_kivi_now)

    async def frames() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        def listen(step: TraceStep) -> None:
            # Called on the worker thread, so hand it across rather than
            # touching the loop's queue directly.
            loop.call_soon_threadsafe(queue.put_nowait, _step_frame(step))

        def work() -> dict[str, Any]:
            try:
                with SessionLocal() as session:
                    outcome = asking.run(
                        session, user_id, request, text=text, now=reference,
                        on_step=listen,
                    )
                    session.commit()
                    return outcome.as_dict()
            finally:
                # Always, including on failure, or the reader waits forever.
                loop.call_soon_threadsafe(queue.put_nowait, done)

        running = asyncio.create_task(run_in_threadpool(work))
        yield _frame("hello", {"request": request, "at": reference.isoformat()})

        waited = 0.0
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                waited += HEARTBEAT_SECONDS
                # A comment line: keeps the connection warm and tells the
                # reader the silence is work rather than a dropped stream.
                yield f": waiting {waited:.0f}s\n\n"
                continue
            if item is done:
                break
            waited = 0.0
            yield _frame("step", item)

        try:
            result = await running
        except Exception as exc:  # noqa: BLE001 - the reader is owed a reason
            yield _frame("error", {"error": f"{type(exc).__name__}: {exc}"})
            # A stream that stops without saying so reads as a dropped
            # connection, and EventSource answers that by reconnecting and
            # running the request again. Say the stream is over.
            yield _frame("done", {"trace_id": None})
            return

        yield _frame("answer", result)
        yield _frame("done", {"trace_id": result.get("trace_id")})

    return StreamingResponse(frames(), media_type="text/event-stream", headers=_SSE)
