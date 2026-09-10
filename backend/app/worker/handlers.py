"""Stage registry: which function runs which job stage.

Each handler runs inside one trace, so every stage of the ingest pipeline is
accounted for whether or not it called a model.
"""

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.embedding import Embedding
from app.models.event import Event
from app.models.job import Job
from app.services import embed, profile, queue
from app.services.consolidate import consolidate_episode
from app.services.episodes import assign_events
from app.services.tracing import start_ingest_trace

Handler = Callable[[Session, Job], dict[str, Any]]

STAGE_EMBED = "embed"
STAGE_EPISODE_ASSIGN = "episode_assign"
STAGE_EPISODE_CONSOLIDATE = "episode_consolidate"
STAGE_PROFILE_REFRESH = profile.REFRESH_STAGE

# Enough to keep one pass short while still amortising the model load.
EMBED_BATCH = 128


def handle_embed(session: Session, job: Job) -> dict[str, Any]:
    """Give every unembedded dictation a vector.

    Runs off the request path rather than inside it: the model is local, so
    this costs no money and cannot be rate limited, but it does cost CPU and
    an ingest call should not wait for it. A dictation is searchable within
    seconds of being spoken, not synchronously with being stored.

    Only events that cleared the junk gate are embedded. A mic test does not
    need to be findable, and putting it in the index means it competes with
    things that do.
    """
    with start_ingest_trace(
        session, user_id=job.user_id, subject_key=job.subject_key
    ) as recorder:
        pending = list(
            session.scalars(
                select(Event)
                .where(
                    Event.user_id == job.user_id,
                    Event.ingest_status != "ignored",
                    Event.id.notin_(
                        select(Embedding.object_id).where(
                            Embedding.user_id == job.user_id,
                            Embedding.object_type == "event",
                            Embedding.model == settings.embedding_model,
                        )
                    ),
                )
                .order_by(Event.occurred_at.desc())
                .limit(EMBED_BATCH)
            ).all()
        )

        written = embed.store(
            session,
            job.user_id,
            [("event", event.id, event.canonical_text or "") for event in pending],
        )

        recorder.step(
            STAGE_EMBED,
            decision=f"{written} events embedded",
            rationale=(
                f"local model {settings.embedding_model}, no provider call"
            ),
            output_summary={"embedded": written, "batch": len(pending)},
        )
        recorder.close("answered")

    # More waiting means another pass. Re-queueing rather than looping keeps
    # one job short, so a large backfill cannot block the rest of the queue.
    if len(pending) >= EMBED_BATCH:
        queue.enqueue(
            session,
            user_id=job.user_id,
            stage=STAGE_EMBED,
            subject_key=job.subject_key,
        )

    return {"embedded": written, "more": len(pending) >= EMBED_BATCH}


def handle_episode_assign(session: Session, job: Job) -> dict[str, Any]:
    """Attach new events to episodes, then queue the ones that closed."""
    with start_ingest_trace(
        session, user_id=job.user_id, subject_key=job.subject_key
    ) as recorder:
        result = assign_events(session, user_id=job.user_id)
        pending = result.pop("needs_consolidation", [])

        recorder.step(
            STAGE_EPISODE_ASSIGN,
            decision=(
                f"{result['assigned']} events into "
                f"{result['episodes_touched']} episodes"
            ),
            rationale=(
                f"{result['closed']} episodes closed on idleness or span; "
                f"{len(pending)} awaiting consolidation"
            ),
            output_summary=result,
        )

        queued = queue.enqueue_many(
            session,
            user_id=job.user_id,
            stage=STAGE_EPISODE_CONSOLIDATE,
            subject_keys=[str(episode_id) for episode_id in pending],
            subject_type="episode",
        )
        recorder.close("answered")

    return {**result, "consolidations_queued": queued}


def handle_episode_consolidate(session: Session, job: Job) -> dict[str, Any]:
    """Summarise one closed episode and store what it is worth remembering."""
    episode_id = uuid.UUID(job.subject_key)

    with start_ingest_trace(
        session, user_id=job.user_id, subject_key=job.subject_key
    ) as recorder:
        result = consolidate_episode(session, episode_id, recorder=recorder)
        recorder.close(
            "answered",
            final_output=(
                f"{result['summary_status']}, {result['created']} new memories"
            ),
        )

    return result


def handle_profile_refresh(session: Session, job: Job) -> dict[str, Any]:
    """Rebuild the always-on profile after the beliefs behind it changed.

    Its own stage rather than part of consolidation: the queue coalesces
    pending jobs by subject, so a run over 135 episodes refreshes once
    instead of 135 times.

    Re-arms itself for tomorrow before returning, so the profile keeps up
    with a day's dictations whether or not consolidation happens to notice a
    belief moved. Currency decays with the clock, so what belongs in the
    profile changes even on a day nobody said anything new.
    """
    with start_ingest_trace(
        session, user_id=job.user_id, subject_key=job.subject_key
    ) as recorder:
        built = profile.refresh(session, job.user_id)
        recorder.step(
            STAGE_PROFILE_REFRESH,
            decision=f"{len(built.style)} style, {len(built.work)} work",
            rationale=f"{built.tokens} of {profile.BUDGET_TOKENS} tokens",
            output_summary={
                "style": len(built.style),
                "work": len(built.work),
                "tokens": built.tokens,
            },
        )
        recorder.close("answered")

    # Tomorrow's rebuild, queued now. This job is 'running' rather than
    # 'pending', so the coalescing index does not treat it as a duplicate.
    queue.enqueue(
        session,
        user_id=job.user_id,
        stage=STAGE_PROFILE_REFRESH,
        subject_key=profile.REFRESH_SUBJECT,
        run_after=datetime.now(timezone.utc) + profile.REFRESH_INTERVAL,
    )

    return {"style": len(built.style), "work": len(built.work), "tokens": built.tokens}


HANDLERS: dict[str, Handler] = {
    STAGE_EMBED: handle_embed,
    STAGE_EPISODE_ASSIGN: handle_episode_assign,
    STAGE_EPISODE_CONSOLIDATE: handle_episode_consolidate,
    STAGE_PROFILE_REFRESH: handle_profile_refresh,
}
