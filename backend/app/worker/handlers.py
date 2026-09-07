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
from app.services.episodes import rebuild_group
from app.services.summarise import summarise_episode
from app.services.tracing import start_ingest_trace

Handler = Callable[[Session, Job], dict[str, Any]]

STAGE_EPISODE_ASSIGN = "episode_assign"
STAGE_EPISODE_SUMMARISE = "episode_summarise"


def handle_episode_assign(session: Session, job: Job) -> dict[str, Any]:
    """Recompute a group's episodes, then queue summaries for closed ones."""
    with start_ingest_trace(
        session, user_id=job.user_id, subject_key=job.subject_key
    ) as recorder:
        result = rebuild_group(session, user_id=job.user_id, group_key=job.subject_key)
        pending = result.pop("needs_summary", [])

        recorder.step(
            STAGE_EPISODE_ASSIGN,
            decision=f"{result['episodes']} episodes from {result['events']} events",
            rationale=(
                f"deterministic grouping of {job.subject_key}: "
                f"{result['open']} still open, {len(pending)} awaiting summary"
            ),
            output_summary={k: v for k, v in result.items() if k != "group_key"},
        )

        queued = queue.enqueue_many(
            session,
            user_id=job.user_id,
            stage=STAGE_EPISODE_SUMMARISE,
            subject_keys=[str(episode_id) for episode_id in pending],
            subject_type="episode",
        )
        recorder.close("answered")

    return {**result, "summaries_queued": queued}


def handle_episode_summarise(session: Session, job: Job) -> dict[str, Any]:
    """Give one episode a title and summary, calling a model only if needed."""
    episode_id = uuid.UUID(job.subject_key)

    with start_ingest_trace(
        session, user_id=job.user_id, subject_key=job.subject_key
    ) as recorder:
        result = summarise_episode(session, episode_id, recorder=recorder)
        recorder.close("answered", final_output=result["summary_status"])

    return result


HANDLERS: dict[str, Handler] = {
    STAGE_EPISODE_ASSIGN: handle_episode_assign,
    STAGE_EPISODE_SUMMARISE: handle_episode_summarise,
}
