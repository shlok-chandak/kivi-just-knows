"""Finding the things that might answer a question.

Structured filters run first and vector search runs inside what they leave.
That order is the whole design. "Yesterday" and "Aditya" are constraints, not
suggestions -- embed the whole question and search globally and you get last
month's conversation with somebody else, ranked confidently.

Two arms, because they fail differently. Vector search finds a claim worded
nothing like the question and misses an unusual proper noun; trigram matching
finds the proper noun and understands nothing. Neither is reliable alone, so
both run and their results are merged.

Similarity is not the answer, only the shortlist. Ordering the shortlist is
ranking.py's job: similarity says what is *about* the question, ranking says
what is still *true*. Similarity alone will offer a price from July with the
same confidence as the one from last week.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.embedding import Embedding
from app.models.episode import Episode
from app.models.event import Event
from app.models.memory import Memory
from app.services import embed, ranking
from app.services.ablation import NONE, Ablation

logger = logging.getLogger("kivi.retrieval")

Kind = Literal["memory", "event", "episode"]

# Below this a vector match is noise. Placed between two measured
# distributions rather than chosen: on this model, unrelated text peaks at
# 0.109 and paraphrase of the same claim bottoms out at 0.378, so the
# midpoint is 0.243. Re-derive this if the model changes -- do not tune it
# against a corpus (PROJECT_CONTEXT §6.3a).
#
#   paraphrase (same claim)   min 0.378   mean 0.691
#   unrelated                 max 0.109   mean 0.086
MIN_SIMILARITY = 0.243

# How much of the shortlist each arm may contribute before merging.
VECTOR_LIMIT = 40
LEXICAL_LIMIT = 20

# Trigram similarity is a different scale from cosine and means something
# different -- spelling overlap, not meaning. A lexical hit is treated as a
# fixed, modest similarity so it can join the shortlist without pretending to
# be a semantic match.
LEXICAL_SIMILARITY = 0.35
MIN_TRIGRAM = 0.3

# How many matched items are kept for inspection. Capped so a broad question
# does not carry hundreds of rows into every trace that is written.
MATCHED_DETAIL = 50


@dataclass
class Candidate:
    """One thing that might help answer, and why it is here."""

    kind: Kind
    id: uuid.UUID
    text: str
    occurred_at: datetime | None
    similarity: float
    score: float
    matched_by: set[str] = field(default_factory=set)
    memory_type: str | None = None
    confidence: float | None = None

    # Old, but still what we believe. A preference from March is not wrong.
    stale: bool = False

    # Replaced by a later claim. Different from stale in the way that
    # matters: this one is no longer true, and saying it as though it were
    # is the failure the whole supersession mechanism exists to prevent.
    superseded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "text": self.text[:200],
            "similarity": round(self.similarity, 3),
            "score": round(self.score, 3),
            "matched_by": sorted(self.matched_by),
            "stale": self.stale,
            "superseded": self.superseded,
        }


# How much a raw record's age discounts it. Halves roughly every month,
# and never reaches zero -- an old dictation is still the only record of
# what was said.
RECENCY_HALF_LIFE_DAYS = 30.0
RECENCY_FLOOR = 0.6


def freshness(occurred_at: datetime | None, now: datetime) -> float:
    """The quality factor for a raw record: a dictation or an episode.

    Shared by both on purpose. Every kind's score has to be similarity
    multiplied by something in the same range, or the kinds cannot be
    compared -- and episodes were the one kind multiplied by nothing,
    which is not "no opinion", it is a claim of perfect confidence and
    perfect freshness. A belief scored 0.70 x 0.63 lost to a summary
    scored 0.57 x 1.0, so the understanding layer was outranked by the
    transcript it was drawn from on every question.

    Neither kind carries a belief, so age is the only quality signal
    available for them. Memories use confidence and currency instead,
    which live in ranking.py and land in the same range.
    """
    if occurred_at is None:
        return RECENCY_FLOOR
    age_days = max((now - occurred_at).total_seconds() / 86400.0, 0.0)
    recency = 1.0 / (1.0 + age_days / RECENCY_HALF_LIFE_DAYS)
    return RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * recency


def _order(candidate: Candidate) -> tuple[float, bool]:
    """By score, with a live belief winning a tie against a replaced one.

    Relevance has to lead. Ranking every replaced belief below every live
    one was tried and is worse than it sounds: a replaced price matching at
    0.78 lands underneath an unrelated live note matching at 0.25, falls
    outside the context window, and history becomes unreachable again --
    the same bug, one layer further in.

    Being replaced is not handled by burying it. It is handled by saying
    so: the source is labelled REPLACED, and the answer prompt is built to
    use the current value and name the change.
    """
    return (-candidate.score, candidate.superseded)


@dataclass
class Retrieved:
    """What the search found, and what it had to do to find it."""

    candidates: list[Candidate]
    widened: list[str] = field(default_factory=list)
    filters_applied: dict[str, Any] = field(default_factory=dict)
    filters_not_applied: list[str] = field(default_factory=list)

    # How many matched before the shortlist was cut, and of what. Kept
    # because "answered from 3 sources" does not say whether three was all
    # there was or three survived out of thirty -- and those are different
    # answers to "why did it say that".
    considered: int = 0
    by_kind: dict[str, int] = field(default_factory=dict)

    # Everything that matched, in rank order, not only the slice that
    # survived the cut. The count alone cannot answer "what did you drop
    # and why" -- and that is the question a wrong answer raises first.
    matched: list[Candidate] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.candidates


# --- structured narrowing ---------------------------------------------------


def _apply_filters(
    stmt: Select,
    column: Any,
    *,
    since: datetime | None,
    until: datetime | None,
) -> Select:
    """Time is a hard filter. It is arithmetic, never a model's judgement."""
    if since is not None:
        stmt = stmt.where(column >= since)
    if until is not None:
        stmt = stmt.where(column <= until)
    return stmt


def _vector_arm(
    session: Session,
    user_id: uuid.UUID,
    object_type: Kind,
    query_vector: list[float],
    *,
    ids: Sequence[uuid.UUID] | None,
    limit: int,
) -> dict[uuid.UUID, float]:
    """Cosine similarity for candidates the filters already allowed.

    `ids` is the narrowed set. Passing None means no structured filter
    applied, which is the only case where a global search is correct.
    """
    distance = Embedding.vector.cosine_distance(query_vector)
    stmt = (
        select(Embedding.object_id, distance.label("distance"))
        .where(
            Embedding.user_id == user_id,
            Embedding.object_type == object_type,
            # A vector from another model is not comparable to this one.
            Embedding.model == settings.embedding_model,
        )
        .order_by(distance)
        .limit(limit)
    )
    if ids is not None:
        if not ids:
            return {}
        stmt = stmt.where(Embedding.object_id.in_(list(ids)))

    return {
        object_id: 1.0 - float(dist)
        for object_id, dist in session.execute(stmt).all()
        if 1.0 - float(dist) >= MIN_SIMILARITY
    }


def _lexical_arm(
    session: Session,
    column: Any,
    id_column: Any,
    stmt: Select,
    query: str,
    limit: int,
) -> dict[uuid.UUID, float]:
    """Trigram matching, for the words a vector will not preserve.

    An unusual proper noun -- a customer name, a ticket reference -- barely
    moves an embedding but is exactly what someone searches for.
    """
    scored = stmt.add_columns(
        func.similarity(func.coalesce(column, ""), query).label("trigram")
    ).order_by(func.similarity(func.coalesce(column, ""), query).desc()).limit(limit)

    found: dict[uuid.UUID, float] = {}
    for row in session.execute(scored).all():
        if float(row.trigram or 0) >= MIN_TRIGRAM:
            found[row[0]] = LEXICAL_SIMILARITY
    return found


# --- the searches -----------------------------------------------------------


def search_memories(
    session: Session,
    user_id: uuid.UUID,
    query: str,
    *,
    now: datetime,
    since: datetime | None = None,
    until: datetime | None = None,
    memory_types: Sequence[str] | None = None,
    include_candidates: bool = False,
    include_superseded: bool = False,
    limit: int = 10,
    cuts: Ablation = NONE,
) -> list[Candidate]:
    """Durable beliefs that might answer this.

    Candidates are excluded unless asked for, because an inference heard
    once should not be stated as fact.

    Superseded beliefs are included when asked for, and then always sort
    below live ones. Excluding them outright was simpler and wrong: "what
    was the price before we raised it" is a real question, and a store that
    keeps history and cannot retrieve it is keeping it for nobody. The
    danger was never that history is reachable, it is that history is
    mistaken for the present -- which is handled by labelling and ordering,
    not by hiding.
    """
    statuses = ["active"]
    if include_candidates:
        statuses.append("candidate")
    if include_superseded:
        statuses.append("superseded")

    allowed = select(Memory.id).where(Memory.user_id == user_id)
    allowed = allowed.where(Memory.status.in_(statuses))
    if memory_types:
        allowed = allowed.where(Memory.type.in_(list(memory_types)))
    # Withheld for this run only. Nothing is deleted, so the same question
    # asked normally still finds them.
    if cuts.exclude_memory_ids:
        allowed = allowed.where(Memory.id.notin_(list(cuts.exclude_memory_ids)))
    allowed = _apply_filters(
        allowed, Memory.last_reinforced_at, since=since, until=until
    )

    ids = list(session.scalars(allowed))
    if not ids:
        return []

    hits = _vector_arm(
        session, user_id, "memory", embed.encode_one(query), ids=ids,
        limit=VECTOR_LIMIT,
    )
    lexical = _lexical_arm(
        session,
        Memory.content,
        Memory.id,
        select(Memory.id).where(Memory.id.in_(ids)),
        query,
        LEXICAL_LIMIT,
    )

    candidates: list[Candidate] = []
    for memory in session.scalars(
        select(Memory).where(Memory.id.in_(set(hits) | set(lexical)))
    ):
        similarity = max(hits.get(memory.id, 0.0), lexical.get(memory.id, 0.0))
        matched = {arm for arm, found in
                   (("vector", hits), ("lexical", lexical)) if memory.id in found}
        replaced = memory.status == "superseded"

        # An expired commitment is not offered as current. It stays in the
        # store and stays answerable when asked about directly.
        #
        # Not applied to a replaced belief. `valid_until` carries two
        # meanings: a commitment's own deadline, and the moment a belief
        # stopped being true. Every superseded row has the second, so
        # checking expiry here would drop the whole of history again,
        # one line further down than last time.
        if not replaced and ranking.is_expired(memory, now):
            continue

        candidates.append(
            Candidate(
                kind="memory",
                id=memory.id,
                text=memory.content,
                occurred_at=memory.last_reinforced_at,
                similarity=similarity,
                score=similarity * ranking.score(memory, now),
                matched_by=matched,
                memory_type=memory.type,
                confidence=float(memory.posterior_mean or 0.0),
                stale=ranking.is_stale(memory, now),
                superseded=replaced,
            )
        )

    candidates.sort(key=_order)
    return candidates[:limit]


def search_events(
    session: Session,
    user_id: uuid.UUID,
    query: str,
    *,
    now: datetime,
    since: datetime | None = None,
    until: datetime | None = None,
    apps: Sequence[str] | None = None,
    unconsolidated_only: bool = True,
    limit: int = 10,
) -> list[Candidate]:
    """The dictations themselves.

    Defaults to events no memory has been drawn from yet. That is the fast
    path: something said five minutes ago is answerable by quoting it, with
    no model call and nothing waiting for an episode to close. Once a claim
    has been consolidated the memory answers instead, so the two cannot
    both surface the same thing.
    """
    allowed = select(Event.id).where(
        Event.user_id == user_id,
        Event.ingest_status != "ignored",
    )
    if unconsolidated_only:
        allowed = allowed.where(Event.consolidated_at.is_(None))
    if apps:
        allowed = allowed.where(Event.app.in_(list(apps)))
    allowed = _apply_filters(allowed, Event.occurred_at, since=since, until=until)

    ids = list(session.scalars(allowed))
    if not ids:
        return []

    hits = _vector_arm(
        session, user_id, "event", embed.encode_one(query), ids=ids,
        limit=VECTOR_LIMIT,
    )
    lexical = _lexical_arm(
        session,
        Event.formatted_text,
        Event.id,
        select(Event.id).where(Event.id.in_(ids)),
        query,
        LEXICAL_LIMIT,
    )

    candidates: list[Candidate] = []
    for event in session.scalars(
        select(Event).where(Event.id.in_(set(hits) | set(lexical)))
    ):
        similarity = max(hits.get(event.id, 0.0), lexical.get(event.id, 0.0))
        candidates.append(
            Candidate(
                kind="event",
                id=event.id,
                text=event.canonical_text or "",
                occurred_at=event.occurred_at,
                similarity=similarity,
                score=similarity * freshness(event.occurred_at, now),
                matched_by={arm for arm, found in
                            (("vector", hits), ("lexical", lexical))
                            if event.id in found},
            )
        )

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def search_episodes(
    session: Session,
    user_id: uuid.UUID,
    query: str,
    *,
    now: datetime,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 5,
) -> list[Candidate]:
    """Episode summaries, for questions about a conversation rather than a fact.

    Scored like a dictation rather than like a belief, because that is what
    a summary is: a record of what was said, not a conclusion drawn from it.
    A summary of the conversation where a price was set still quotes the old
    price forever, so it must not outrank the belief that superseded it.
    """
    allowed = select(Episode.id).where(
        Episode.user_id == user_id, Episode.summary.is_not(None)
    )
    allowed = _apply_filters(allowed, Episode.started_at, since=since, until=until)
    ids = list(session.scalars(allowed))
    if not ids:
        return []

    hits = _vector_arm(
        session, user_id, "episode", embed.encode_one(query), ids=ids,
        limit=VECTOR_LIMIT,
    )
    candidates = [
        Candidate(
            kind="episode",
            id=episode.id,
            text=episode.summary or "",
            occurred_at=episode.started_at,
            similarity=hits[episode.id],
            score=hits[episode.id] * freshness(episode.started_at, now),
            matched_by={"vector"},
        )
        for episode in session.scalars(
            select(Episode).where(Episode.id.in_(hits))
        )
    ]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


# --- the whole search -------------------------------------------------------

WIDEN_DAYS = 3


def search(
    session: Session,
    user_id: uuid.UUID,
    query: str,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    apps: Sequence[str] | None = None,
    memory_types: Sequence[str] | None = None,
    person: str | None = None,
    include_superseded: bool = True,
    limit: int = 12,
    cuts: Ablation = NONE,
) -> Retrieved:
    """Everything that might answer, most useful first.

    Widens once when the filters leave nothing, and records that it did. A
    silent widen turns "nothing yesterday" into a confident answer about last
    week, which is worse than saying nothing was found.
    """
    now = now or datetime.now(timezone.utc)
    widened: list[str] = []
    not_applied: list[str] = []

    # The vector-only comparison: let similarity decide with no narrowing
    # first, which is the arrangement this design argues against.
    if cuts.skip_filters:
        since = until = None
        apps = memory_types = None
        not_applied.append("all filters (ablation)")

    # Recipient is not captured, so a question naming a person cannot be
    # filtered by one. The name still helps as search text, but the answer
    # has to say the filter was not applied rather than implying it was.
    if person:
        not_applied.append(f"person={person}")

    def run(app_filter, start, end) -> list[Candidate]:
        return (
            search_memories(
                session, user_id, query, now=now, since=start, until=end,
                memory_types=memory_types, limit=limit, cuts=cuts,
                include_superseded=include_superseded,
            )
            + search_events(
                session, user_id, query, now=now, since=start, until=end,
                apps=app_filter, limit=limit,
            )
            + search_episodes(
                session, user_id, query, now=now, since=start, until=end,
                limit=5,
            )
        )

    found = run(apps, since, until)

    if not found and apps:
        widened.append("dropped app filter")
        found = run(None, since, until)

    if not found and (since or until):
        widened.append(f"widened time by ±{WIDEN_DAYS} days")
        found = run(
            None,
            since - timedelta(days=WIDEN_DAYS) if since else None,
            until + timedelta(days=WIDEN_DAYS) if until else None,
        )

    found.sort(key=_order)

    by_kind: dict[str, int] = {}
    for candidate in found:
        by_kind[candidate.kind] = by_kind.get(candidate.kind, 0) + 1

    return Retrieved(
        candidates=found[:limit],
        considered=len(found),
        by_kind=by_kind,
        matched=found[:MATCHED_DETAIL],
        widened=widened,
        filters_applied={
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "apps": list(apps) if apps else None,
            "memory_types": list(memory_types) if memory_types else None,
        },
        filters_not_applied=not_applied,
    )
