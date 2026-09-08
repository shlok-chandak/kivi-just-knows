"""Stage registry: which function runs which job stage.

Each handler runs inside one trace, so every stage of the ingest pipeline is
accounted for whether or not it called a model.
"""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.job import Job
from app.services import queue
from app.services.consolidate import consolidate_episode
from app.services.episodes import assign_events
from app.services.tracing import start_ingest_trace

Handler = Callable[[Session, Job], dict[str, Any]]

STAGE_EPISODE_ASSIGN = "episode_assign"
STAGE_EPISODE_CONSOLIDATE = "episode_consolidate"


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
    STAGE_EPISODE_ASSIGN: handle_episode_assign,
    STAGE_EPISODE_CONSOLIDATE: handle_episode_consolidate,
}
