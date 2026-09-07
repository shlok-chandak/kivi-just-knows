"""Worker loop: claim a job, run it, record the outcome.

One transaction per job. A claimed job stays locked until that transaction
ends, so no other worker can take it.
"""

import logging
import signal
import time
from types import FrameType

from app.db.session import SessionLocal
from app.models.job import Job
from app.services import queue
from app.worker.handlers import HANDLERS

logger = logging.getLogger("kivi.worker")

IDLE_SLEEP_SECONDS = 1.0

_shutdown = False


def _request_shutdown(signum: int, frame: FrameType | None) -> None:
    """Finish the job in flight, then stop."""
    global _shutdown
    _shutdown = True
    logger.info("shutdown requested (signal %s)", signum)


def run_once() -> bool:
    """Process one job. Returns False when the queue had nothing due."""
    session = SessionLocal()
    try:
        job = queue.claim(session, stages=list(HANDLERS))
        if job is None:
            session.rollback()
            return False

        stage, subject = job.stage, job.subject_key
        handler = HANDLERS.get(stage)

        if handler is None:
            queue.fail(session, job, f"no handler for stage {stage!r}")
            session.commit()
            logger.error("no handler for stage %s", stage)
            return True

        try:
            result = handler(session, job)
            queue.complete(session, job)
            session.commit()
            logger.info("%s %s -> %s", stage, subject, result)
        except Exception as exc:  # noqa: BLE001 - the queue records the reason
            session.rollback()
            # The rollback also undid the claim, so the attempt count and
            # status need reapplying to the freshly-read row.
            failing = session.get(Job, job.id)
            if failing is not None:
                failing.attempts += 1
                queue.fail(session, failing, f"{type(exc).__name__}: {exc}")
                session.commit()
            logger.exception("%s %s failed", stage, subject)

        return True
    finally:
        session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    logger.info("worker started, stages: %s", ", ".join(sorted(HANDLERS)))

    while not _shutdown:
        try:
            did_work = run_once()
        except Exception:
            logger.exception("worker loop error")
            did_work = False

        if not did_work:
            time.sleep(IDLE_SLEEP_SECONDS)

    logger.info("worker stopped")


if __name__ == "__main__":
    main()
