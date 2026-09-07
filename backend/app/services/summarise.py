"""Episode summarisation, gated so most episodes never reach a model.

Structure is free and runs on everything; inference is earned. A single-line
dictation is summarised by itself: cheaper, and a better embedding target
than a paraphrase would be.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.client import SMALL, get_client
from app.llm.prompts import SUMMARISE_SYSTEM, render_episode_for_summary
from app.models.episode import Episode, EpisodeEvent
from app.models.event import Event
from app.schemas.summary import EpisodeSummaryOut
from app.services.tracing import TraceRecorder

logger = logging.getLogger("kivi.summarise")

STAGE = "episode_summarise"

# Two short dictations are cheaper to keep verbatim than to paraphrase.
VERBATIM_PAIR_MAX_CHARS = 200

# Deterministic title for episodes that skip the model.
TITLE_WORD_LIMIT = 8


def _fallback_title(text: str) -> str:
    words = text.split()
    title = " ".join(words[:TITLE_WORD_LIMIT])
    return title.rstrip(".,;:!?") or "Untitled"


def episode_texts(session: Session, episode_id: uuid.UUID) -> list[str]:
    """The dictations in a sitting, in order, skipping any without content."""
    rows = session.execute(
        select(Event.formatted_text)
        .join(EpisodeEvent, EpisodeEvent.event_id == Event.id)
        .where(EpisodeEvent.episode_id == episode_id)
        .order_by(EpisodeEvent.seq)
    ).scalars()
    return [text for text in rows if text and text.strip()]


def summarise_episode(
    session: Session,
    episode_id: uuid.UUID,
    recorder: TraceRecorder | None = None,
) -> dict[str, Any]:
    """Fill in an episode's title and summary, calling a model only if needed."""
    episode = session.get(Episode, episode_id)
    if episode is None:
        raise ValueError(f"no such episode: {episode_id}")

    texts = episode_texts(session, episode_id)
    outcome = _apply_gate(episode, texts)

    if recorder is not None:
        recorder.step(
            STAGE,
            decision=outcome["decision"],
            rationale=outcome["rationale"],
            usage=outcome.get("usage"),
            input_summary={"event_count": episode.event_count, "chars": sum(map(len, texts))},
            output_summary={"summary_status": episode.summary_status},
        )

    session.flush()
    return {
        "episode_id": str(episode_id),
        "summary_status": episode.summary_status,
        "called_model": outcome.get("usage") is not None,
    }


def _apply_gate(episode: Episode, texts: list[str]) -> dict[str, Any]:
    """Choose how this episode gets its summary, and apply it."""

    # Nothing with content: no summary is possible and none is needed.
    if not texts:
        episode.summary = None
        episode.title = None
        episode.summary_status = "skipped"
        return {
            "decision": "skipped",
            "rationale": "no dictation in this episode carries text",
        }

    # One dictation is already its own best summary.
    if len(texts) == 1:
        episode.summary = texts[0]
        episode.title = _fallback_title(texts[0])
        episode.summary_status = "verbatim"
        return {
            "decision": "verbatim",
            "rationale": "single dictation: the text is used as its own summary",
        }

    combined = " ".join(texts)
    if len(texts) == 2 and len(combined) < VERBATIM_PAIR_MAX_CHARS:
        episode.summary = combined
        episode.title = _fallback_title(texts[0])
        episode.summary_status = "verbatim"
        return {
            "decision": "verbatim",
            "rationale": (
                f"two short dictations ({len(combined)} chars): concatenated "
                "rather than paraphrased"
            ),
        }

    prompt = render_episode_for_summary(
        app=episode.app,
        started_at=episode.started_at.isoformat(),
        texts=texts,
    )
    completion = get_client().structured(
        prompt=prompt,
        schema=EpisodeSummaryOut,
        system=SUMMARISE_SYSTEM,
        tier=SMALL,
    )
    result: EpisodeSummaryOut = completion.value

    episode.title = result.title
    episode.summary = result.summary
    episode.topic_tags = result.topic_tags
    episode.summary_status = "generated"

    return {
        "decision": "generated",
        "rationale": (
            f"{len(texts)} dictations, {len(combined)} chars: summarised by model"
        ),
        "usage": completion.usage,
    }
