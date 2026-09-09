"""Stage registry: which function runs which job stage.

Each handler runs inside one trace, so every stage of the ingest pipeline is
accounted for whether or not it called a model.
"""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.embedding import Embedding
from app.models.event import Event
from app.models.job import Job
from app.services import embed, queue
from app.services.consolidate import consolidate_episode
from app.services.episodes import assign_events
from app.services.tracing import start_ingest_trace

Handler = Callable[[Session, Job], dict[str, Any]]

STAGE_EMBED = "embed"
STAGE_EPISODE_ASSIGN = "episode_assign"
STAGE_EPISODE_CONSOLIDATE = "episode_consolidate"

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


HANDLERS: dict[str, Handler] = {
    STAGE_EMBED: handle_embed,
    STAGE_EPISODE_ASSIGN: handle_episode_assign,
    STAGE_EPISODE_CONSOLIDATE: handle_episode_consolidate,
}
