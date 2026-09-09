"""Turning a closed episode into a summary and a set of claims.

One model call, not two. Deciding what mattered in a stretch of activity is
the same judgement for both halves, and one call halves the prompt overhead
and gives a single unit to retry.

The episode is shown to the model as its sittings, which lines were one
conversation, which were somewhere else, so structure that cost nothing to
compute is not thrown away before the expensive step.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.client import SMALL, get_client
from app.llm.prompts import CONSOLIDATE_SYSTEM, render_episode_for_consolidation
from app.models.episode import Episode
from app.models.event import Event
from app.models.rejected import RejectedCandidate
from app.schemas.consolidation import ConsolidationOut
from app.models.memory import Memory, MemoryEvidence
from app.services import claims, embed
from app.services.episodes import events_in_episode, partition_into_sittings
from app.services.gate import decide, fallback_title
from app.services.tracing import TraceRecorder

logger = logging.getLogger("kivi.consolidate")

STAGE = "episode_consolidate"
RULE_NOTHING_TO_EXTRACT = "no_extractable_content"

# Distinct from the ingest rule: this one got past the deterministic layer and
# had to be removed after being read. Collapsing the two would hide how often
# that happens.
SENSITIVE_ON_REVIEW = "sensitive_on_review"


def consolidate_episode(
    session: Session,
    episode_id: uuid.UUID,
    recorder: TraceRecorder | None = None,
) -> dict[str, Any]:
    """Summarise one episode and store what it is worth remembering."""
    episode = session.get(Episode, episode_id)
    if episode is None:
        raise ValueError(f"no such episode: {episode_id}")

    events = events_in_episode(session, episode_id)
    citable = [event for event in events if (event.canonical_text or "").strip()]
    texts = [event.canonical_text for event in citable]

    verdict, rationale = decide(texts)

    if verdict == "skipped":
        _refuse(session, episode, citable, texts, verdict, rationale)
        _record(recorder, verdict, rationale, events, texts, created=0, reinforced=0)
        session.flush()
        return {
            "episode_id": str(episode_id),
            "summary_status": episode.summary_status,
            "created": 0,
            "reinforced": 0,
            "called_model": False,
        }

    if verdict == "verbatim":
        episode.title = fallback_title(texts[0])
        episode.summary = " ".join(texts)
        episode.summary_status = "verbatim"
        _mark_consolidated(session, episode_id)
        _record(recorder, verdict, rationale, events, texts, created=0, reinforced=0)
        session.flush()
        return {
            "episode_id": str(episode_id),
            "summary_status": "verbatim",
            "created": 0,
            "reinforced": 0,
            "called_model": False,
        }

    sittings = partition_into_sittings(citable)
    completion = get_client().structured(
        prompt=render_episode_for_consolidation(
            started_at=episode.started_at.isoformat(),
            sittings=sittings,
        ),
        schema=ConsolidationOut,
        system=CONSOLIDATE_SYSTEM,
        tier=SMALL,
    )
    result: ConsolidationOut = completion.value

    # A lone dictation is its own best summary: a paraphrase of it embeds
    # worse and says no more. The call was made for the claims, not the prose.
    if len(texts) == 1:
        episode.title = fallback_title(texts[0])
        episode.summary = texts[0]
        episode.summary_status = "verbatim"
    else:
        episode.title = result.title
        episode.summary = result.summary
        episode.summary_status = "generated"
    episode.topic_tags = result.topic_tags

    purged = _purge_sensitive(session, episode, result.sensitive_indexes, citable)

    created = reinforced = refused = superseded = 0
    for candidate in result.memories:
        # Claims cite dictations by position, so `citable` must keep its shape
        # even after a purge -- removing an entry would silently renumber
        # every claim after it onto the wrong dictation.
        sources = claims.resolve_sources(candidate.source_indexes, citable)
        if any(event.id in purged for event in sources):
            # Deleting the dictation is not enough if what was derived from it
            # survives: the claim can carry the very detail that was refused.
            logger.warning("dropping claim drawn from purged content")
            refused += 1
            continue

        outcome = claims.persist(session, episode, candidate, citable)
        if outcome == "created":
            created += 1
        elif outcome == "reinforced":
            reinforced += 1
        elif outcome == "superseded":
            # A belief replaced rather than added. Counted apart from both
            # because it is the one outcome that changes what the system
            # already held, and a run that quietly does a lot of it is
            # either tracking a real change of mind or over-merging.
            superseded += 1
            created += 1
        else:
            refused += 1

    if not result.memories:
        # The model looked and found nothing durable. That is a better ignore
        # record than a prefilter guess, because something actually read it.
        session.add(
            RejectedCandidate(
                user_id=episode.user_id,
                episode_id=episode_id,
                event_id=citable[0].id if citable else None,
                candidate={"episode_text": " ".join(texts)[:500]},
                rejection_rule=RULE_NOTHING_TO_EXTRACT,
                rationale="model found nothing durable in this episode",
            )
        )

    _embed_derived(session, episode)
    _mark_consolidated(session, episode_id)
    _record(
        recorder,
        verdict,
        rationale,
        events,
        texts,
        created=created,
        reinforced=reinforced,
        refused=refused,
        proposed=len(result.memories),
        sittings=len(sittings),
        usage=completion.usage,
    )

    session.flush()
    return {
        "episode_id": str(episode_id),
        "summary_status": episode.summary_status,
        "created": created,
        "reinforced": reinforced,
        "superseded": superseded,
        "refused": refused,
        "proposed": len(result.memories),
        "called_model": True,
    }


def _embed_derived(session: Session, episode: Episode) -> None:
    """Index what this episode produced, so it can be found.

    Both halves matter and they answer different questions. An episode
    summary answers "what was that conversation about"; a memory answers
    "what do I believe". Indexing only one of them would leave the other
    reachable by exact wording alone.

    Best effort: the model is local, so a failure here is a bug rather than
    an outage, and losing an index entry must not lose the memory it points
    at. The embed stage picks up anything missed on its next pass.
    """
    items: list[tuple[str, uuid.UUID, str]] = []
    if episode.summary:
        items.append(("episode", episode.id, episode.summary))

    for memory in session.scalars(
        select(Memory).where(
            Memory.user_id == episode.user_id,
            Memory.id.in_(
                select(MemoryEvidence.memory_id).where(
                    MemoryEvidence.episode_id == episode.id
                )
            ),
        )
    ):
        items.append(("memory", memory.id, memory.content))

    try:
        embed.store(session, episode.user_id, items)
    except Exception:  # noqa: BLE001 - indexing must not lose a memory
        logger.exception("embedding failed for episode %s", episode.id)


def _purge_sensitive(
    session: Session,
    episode: Episode,
    indexes: list[int],
    citable: list[Event],
) -> set[uuid.UUID]:
    """Delete dictations the model judged sensitive, and log that it happened.

    The second net. Ingest refuses what a pattern can recognise; meaning that
    only emerges in context is caught here, by the model already reading the
    episode -- so it costs no extra call.

    The events are deleted outright rather than flagged. A flagged row is
    still the text, still in the database, and still one bug away from being
    read; the promise this build makes is that the content is not kept.
    """
    if settings.store_sensitive_content or not indexes:
        return set()

    doomed = {
        citable[index - 1] for index in indexes if 1 <= index <= len(citable)
    }
    if not doomed:
        return set()

    for event in doomed:
        session.add(
            RejectedCandidate(
                user_id=episode.user_id,
                episode_id=episode.id,
                app=event.app,
                occurred_at=event.occurred_at,
                candidate=None,
                rejection_rule=SENSITIVE_ON_REVIEW,
                rationale=(
                    "sensitive on review: the ingest rules did not recognise "
                    "this, and it was removed after being read"
                ),
            )
        )
        logger.warning(
            "purging event %s: sensitive content missed at ingest", event.id
        )

    doomed_ids = {event.id for event in doomed}
    for event in doomed:
        session.delete(event)
    session.flush()
    return doomed_ids


def _refuse(
    session: Session,
    episode: Episode,
    citable: list[Event],
    texts: list[str],
    verdict: str,
    rationale: str,
) -> None:
    """Record why an episode produced nothing, and leave it unconsolidated.

    Events stay unconsolidated deliberately. Nothing was learned from them, so
    the fast path should keep answering from the dictations themselves rather
    than from a memory that does not exist.
    """
    episode.title = None
    episode.summary = None
    episode.topic_tags = None
    episode.summary_status = verdict

    session.add(
        RejectedCandidate(
            user_id=episode.user_id,
            episode_id=episode.id,
            event_id=citable[0].id if citable else None,
            app=citable[0].app if citable else None,
            occurred_at=episode.started_at,
            candidate={"episode_text": " ".join(texts)[:500]},
            rejection_rule=RULE_NOTHING_TO_EXTRACT,
            rationale=rationale,
        )
    )


def _mark_consolidated(session: Session, episode_id: uuid.UUID) -> None:
    """Hand these events over to the memories made from them.

    Until this runs, the fast path answers from the dictations directly. After
    it, the memory answers and the raw events stop competing with it.
    """
    session.execute(
        update(Event)
        .where(Event.episode_id == episode_id)
        .values(consolidated_at=func.now())
    )


def _record(
    recorder: TraceRecorder | None,
    verdict: str,
    rationale: str,
    events: list[Event],
    texts: list[str],
    *,
    created: int,
    reinforced: int,
    refused: int = 0,
    proposed: int = 0,
    sittings: int = 0,
    usage: Any = None,
) -> None:
    if recorder is None:
        return

    detail = rationale
    if proposed:
        detail = (
            f"{rationale}; model proposed {proposed}, created {created}, "
            f"reinforced {reinforced}"
            + (f", refused {refused} without usable provenance" if refused else "")
        )

    recorder.step(
        STAGE,
        decision=verdict,
        rationale=detail,
        usage=usage,
        input_summary={
            "event_count": len(events),
            "chars": sum(len(text) for text in texts),
            "sittings": sittings,
        },
        output_summary={
            "summary_status": verdict,
            "created": created,
            "reinforced": reinforced,
            "refused": refused,
        },
    )
