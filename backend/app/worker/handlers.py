"""Stage registry: which function runs which job stage."""

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.models.job import Job
from app.services.episodes import rebuild_group

Handler = Callable[[Session, Job], dict[str, Any]]

STAGE_EPISODE_ASSIGN = "episode_assign"


def handle_episode_assign(session: Session, job: Job) -> dict[str, Any]:
    """Recompute every episode in the job's group."""
    return rebuild_group(session, user_id=job.user_id, group_key=job.subject_key)


HANDLERS: dict[str, Handler] = {
    STAGE_EPISODE_ASSIGN: handle_episode_assign,
}
