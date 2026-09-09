"""Claim identity, and storing a claim against the dictations behind it.

Two questions live here. What makes two statements the same statement, and
what has to be true before a statement is stored at all.

The first is staged. A claim is a subject and a predicate about it, and
matching either properly needs machinery that does not exist yet: resolving
"Priya" and "Priya Sharma" to one person, and recognising that "pricing is
299" and "we settled on 299" say the same thing. Until then identity is a
normalised hash, which catches restatements in the same words and nothing
else. That limit is reported rather than hidden.

The second is not staged. A claim without a traceable source is refused.
"""

import hashlib
import logging
import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.episode import Episode
from app.models.event import Event
from app.models.memory import (
    HALF_LIFE_DAYS,
    PRIOR_ALPHA,
    PRIOR_BETA,
    WEIGHT_EXPLICIT,
    WEIGHT_INFERRED,
    Memory,
    MemoryEvidence,
)
from app.schemas.extraction import MemoryCandidateOut
from app.services import embed

logger = logging.getLogger("kivi.claims")

# Numbers, money, dates and times. Two claims differing in any of these
# contradict each other, however alike the sentences read -- which is what
# keeps a price change from being absorbed into the price it replaced.
# Case-insensitive, and that is not cosmetic: a capitalised month is how a
# month is normally written, so matching only lowercase made "moved to
# November" versus "moved to October" read as the same claim -- precisely the
# failure this guard exists to catch.
_VALUES = re.compile(
    r"[\d]+(?:[.,]\d+)*%?"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b",
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def values_in(text: str) -> frozenset[str]:
    """The numbers and dates a claim commits to."""
    return frozenset(match.group(0).lower() for match in _VALUES.finditer(text))


# Similarity cannot decide whether two claims are the same claim, and the
# measurement is unambiguous about why. On this model:
#
#   paraphrase, same claim      "price is 349" / "pro tier costs 349 a month"
#                               min 0.378   mean 0.691
#   conflict, different value   "price is 349" / "price is 299"
#                               min 0.691   mean 0.845   max 0.957
#
# Conflicts score *higher* than paraphrases, because an embedding is built to
# ignore a two-character difference and here those two characters are the
# entire content. So there is no threshold: any cut-off low enough to catch a
# paraphrase merges every price change into the price it replaced.
#
# Similarity therefore answers a narrower question -- are these about the same
# thing -- and the values decide same or contradictory. That makes supersession
# something the system detects rather than something it stumbles into.
#
# But similarity cannot fully answer the narrower question either, and the
# measurement says so:
#
#   same claim, different words   "standup is at 10am" / "the daily meeting
#                                 is at 10"                          0.511
#   different subjects entirely   "Priya owns the vendor contract" /
#                                 "Pranav is on the checkout bug"    0.406
#
# A tenth of a point apart, and the second is two people doing two different
# things. Unrelated work claims cluster around 0.4 because they share the
# register, the company and the vocabulary -- so there is no threshold that
# admits the paraphrase without risking the false merge.
#
# The cut-off is therefore set high, deliberately, and it under-merges. A
# claim that should have reinforced becomes a second memory instead: that
# costs corroboration, which is visible and recoverable. Merging two claims
# that were never the same invents a fact nobody stated, which is neither.
#
# Raising recall here needs the subject resolved to an entity rather than
# guessed from wording -- which is M5, and is why §6.4 has always described
# claim identity as subject *and* predicate rather than similarity alone.
SAME_TOPIC = 0.62

# Below this two claims are simply unrelated (see retrieval.MIN_SIMILARITY).
UNRELATED = 0.243


def claim_key(subject: str | None, content: str) -> str:
    """The identity of a claim, as far as this build can determine it.

    Deliberately not scoped to an episode or a conversation. Scoping identity
    to where a claim was said means the same statement made in two places
    becomes two beliefs that can never corroborate each other, which is the
    opposite of what a memory system is for.
    """
    material = f"{_normalise(subject or '')}|{_normalise(content)}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def compare(existing: str, incoming: str) -> str:
    """How a new claim relates to one already held.

    Returns "same", "contradicts", or "unrelated". Computes the similarity
    itself, which is the convenient form for a caller holding two strings
    and nothing else.
    """
    return classify(existing, incoming, _similarity(existing, incoming))


def classify(existing: str, incoming: str, similarity: float) -> str:
    """compare(), for a caller that already knows the similarity.

    Split out because the database can return distance alongside the rows it
    matched, so a caller comparing one claim against ten neighbours would
    otherwise re-embed twenty strings to learn what the query already told
    it.

    The order matters. Values are checked before similarity, because a
    differing number is decisive however alike the sentences read -- and on
    numeric claims the sentences read *more* alike when the number changes.
    """
    if similarity < UNRELATED:
        return "unrelated"

    old_values, new_values = values_in(existing), values_in(incoming)

    # Both commit to a value and the values differ: this is a revision, not a
    # restatement, and it is a revision no matter how close the wording is.
    if old_values and new_values and old_values != new_values:
        return "contradicts" if similarity >= SAME_TOPIC else "unrelated"

    if similarity >= SAME_TOPIC:
        return "same"

    # About the same thing but not clearly the same claim. Creating a separate
    # memory loses corroboration; merging invents a fact nobody stated. The
    # first is recoverable and the second is not.
    return "unrelated"


def _similarity(left: str, right: str) -> float:
    """Cosine between two claims. Imported late so tests can stub the model."""
    from app.services import embed

    vectors = embed.encode([left, right])
    a, b = vectors[0], vectors[1]
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0


def half_life_days(memory_type: str) -> int:
    """How fast this kind of claim stops being worth volunteering."""
    return HALF_LIFE_DAYS.get(memory_type, HALF_LIFE_DAYS["fact"])


def quotes_the_source(excerpt: str, sources: list[Event]) -> bool:
    """Whether the excerpt really is a span of a single dictation.

    Checked in code rather than trusted: a model that paraphrases while
    claiming to quote produces evidence that does not support its own claim,
    and one that mangles a character produces a memory quietly wrong about a
    price or a date.

    Each dictation is checked separately. Joining them first would accept a
    "quotation" running across the end of one utterance and the start of
    another -- a span nobody ever said.
    """
    needle = _normalise(excerpt)
    return any(
        needle in _normalise(event.canonical_text or "") for event in sources
    )


def resolve_sources(indexes: list[int], citable: list[Event]) -> list[Event]:
    """Map the model's 1-based dictation numbers onto real events."""
    resolved: list[Event] = []
    for index in indexes:
        if 1 <= index <= len(citable):
            event = citable[index - 1]
            if event not in resolved:
                resolved.append(event)
    return resolved


def persist(
    session: Session,
    episode: Episode,
    candidate: MemoryCandidateOut,
    citable: list[Event],
    *,
    replaces: Memory | None = None,
) -> str:
    """Store one claim with its evidence.

    Returns what happened: "created", "reinforced", "superseded", or
    "refused". The caller reports these separately, because a run that
    reinforced ten beliefs did not learn ten new things.

    `replaces` is a belief the extractor was shown and said this supersedes.
    Optional, because the extractor only sees beliefs close enough to be
    worth showing -- when it names none, similarity still gets its turn.
    """
    sources = resolve_sources(candidate.source_indexes, citable)
    if not sources:
        # A claim with no traceable source is not a weak memory, it is an
        # unfounded one. Refuse it rather than storing something uncitable.
        logger.warning(
            "dropping claim with no resolvable source: %r", candidate.content[:80]
        )
        return "refused"

    explicit = candidate.basis == "explicit"
    excerpt = candidate.excerpt

    # An excerpt that cannot be found in the dictation is not a quotation, so
    # the claim is not a restatement however the model labelled it. Keep the
    # claim, but hold it as a candidate and quote the source instead.
    if not quotes_the_source(excerpt, sources):
        logger.warning(
            "excerpt is not a span of the source, downgrading to candidate: %r",
            excerpt[:80],
        )
        explicit = False
        excerpt = " ".join(event.canonical_text or "" for event in sources)

    weight = WEIGHT_EXPLICIT if explicit else WEIGHT_INFERRED
    key = claim_key(candidate.subject, candidate.content)
    stated_at = _latest(sources)

    # Same words as something already held. Cheap, exact, and no vectors.
    existing = session.scalars(
        select(Memory).where(
            Memory.user_id == episode.user_id,
            Memory.claim_key == key,
            Memory.status != "superseded",
        )
    ).first()

    outcome = "reinforced"
    if existing is None:
        if replaces is not None and replaces.status != "superseded":
            existing, outcome = _replace(
                session, episode, candidate, key, explicit, replaces, stated_at
            )
            match, relation = None, "handled"
        else:
            match, relation = _closest_live(
                session, episode.user_id, candidate.content
            )

        if relation == "same":
            # Different words, one belief. Reinforcing rather than inserting
            # is what stops a claim restated across five apps from becoming
            # five weakly-held beliefs instead of one well-supported one.
            existing = match
        elif relation == "contradicts":
            existing, outcome = _replace(
                session, episode, candidate, key, explicit, match, stated_at
            )
        elif relation != "handled":
            existing = _create(session, episode, candidate, key, explicit)
            outcome = "created"

    added = _record_evidence(session, episode, existing, sources, weight, excerpt)

    if added:
        # Belief follows evidence. Each new supporting dictation raises alpha,
        # so a claim heard again outscores a passing remark -- and a dictation
        # already recorded changes nothing, which is what stops a re-run from
        # manufacturing confidence.
        existing.alpha += weight * added
        existing.observation_count += added
        existing.last_reinforced_at = _latest(sources)
        if explicit and existing.status == "candidate":
            existing.status = "active"

    session.flush()
    return outcome


# How many held beliefs a new claim is compared against. The list is ordered
# by closeness, so a larger number only adds neighbours less alike than ones
# already rejected -- it buys nothing and costs a comparison each.
NEIGHBOURS = 10


def nearby_live(
    session: Session, user_id: uuid.UUID, text: str, *, limit: int = NEIGHBOURS
) -> list[tuple[Memory, float]]:
    """Live beliefs closest in meaning to this text, nearest first.

    Only live ones. A superseded belief offered back as context invites the
    thing it was superseded for -- and reinforcing one would let a replaced
    price climb back into contention.

    Two callers want this. Claim identity asks whether an incoming claim is
    one of these already; extraction asks to be shown them before reading a
    new stretch of dictation, so a fragment like "349 now" can be resolved
    against what the price was.
    """
    live = select(Memory.id).where(
        Memory.user_id == user_id,
        Memory.status != "superseded",
    )
    neighbours = embed.nearest(
        session,
        user_id,
        "memory",
        embed.encode_one(text),
        ids=list(session.scalars(live)),
        limit=limit,
    )

    found: list[tuple[Memory, float]] = []
    for memory_id, similarity in neighbours:
        memory = session.get(Memory, memory_id)
        if memory is not None:
            found.append((memory, similarity))
    return found


def _closest_live(
    session: Session, user_id: uuid.UUID, content: str
) -> tuple[Memory | None, str]:
    """The held belief this claim relates to, and how.

    Nearest first, returning the first neighbour that is not unrelated. On
    numeric claims that ordering works in our favour rather than against
    it: conflicts measure *closer* than paraphrases, because an embedding
    is built to ignore the two characters that are the entire difference,
    so the belief a new value replaces tends to be the nearest one of all.

    Where it fails is a replacement worded differently -- "going with
    Razorpay" against "switching to Cashfree" measures 0.367, far under the
    bar, because knowing those are two answers to one question is world
    knowledge rather than word similarity. That case is handled upstream,
    by showing the extractor what is already believed and letting it say
    what a new claim replaces.
    """
    for memory, similarity in nearby_live(session, user_id, content):
        relation = classify(memory.content, content, similarity)
        if relation != "unrelated":
            return memory, relation

    return None, "unrelated"


def _replace(
    session: Session,
    episode: Episode,
    candidate: MemoryCandidateOut,
    key: str,
    explicit: bool,
    superseded: Memory,
    stated_at: datetime,
) -> tuple[Memory, str]:
    """Record a claim that contradicts one already held.

    Which of the two wins is decided by when they were said, not by which
    arrived first. Episodes are consolidated in queue order, and a retry or
    a backfill can present last month's decision after this month's -- so
    trusting arrival order would let stale news overwrite current news
    exactly when the queue is under stress.

    The loser is kept rather than deleted. "What is the price" and "what
    was the price in June" are different questions, and a system that
    discards superseded values can only answer the first.
    """
    older = superseded.last_reinforced_at
    incoming_is_newer = older is None or stated_at >= older

    memory = _create(session, episode, candidate, key, explicit)

    if incoming_is_newer:
        superseded.status = "superseded"
        superseded.valid_until = stated_at
        logger.info(
            "superseded %r by %r", superseded.content[:60], candidate.content[:60]
        )
        return memory, "superseded"

    # Arrived late and belongs to the past. Stored as history so it stays
    # answerable, but never volunteered as current.
    memory.status = "superseded"
    memory.valid_until = older
    session.flush()
    return memory, "created"


def _latest(sources: list[Event]) -> datetime:
    """When this claim was most recently said.

    Taken from the dictations themselves rather than the episode's end, so
    currency reflects when the user spoke, not when a batch happened to close.
    """
    return max(event.occurred_at for event in sources)


def _create(
    session: Session,
    episode: Episode,
    candidate: MemoryCandidateOut,
    key: str,
    explicit: bool,
) -> Memory:
    """A new claim, with an uninformative prior.

    Status and confidence are separate judgements. Status answers "may this be
    cited?" -- an outright statement is trusted on one hearing, an inference
    waits. Alpha answers "how well corroborated is this?" and is supplied
    entirely by the evidence loop, so creation and reinforcement share one
    path and cannot disagree.
    """
    memory = Memory(
        id=uuid.uuid4(),
        user_id=episode.user_id,
        type=candidate.type,
        content=candidate.content,
        claim_key=key,
        attributes={"subject": candidate.subject} if candidate.subject else None,
        status="active" if explicit else "candidate",
        alpha=PRIOR_ALPHA,
        beta=PRIOR_BETA,
        observation_count=0,
    )
    session.add(memory)
    session.flush()
    return memory


def _record_evidence(
    session: Session,
    episode: Episode,
    memory: Memory,
    sources: list[Event],
    weight: float,
    excerpt: str,
) -> int:
    """Add rows for dictations not already recorded against this claim."""
    added = 0
    for event in sources:
        already = session.scalars(
            select(MemoryEvidence).where(
                MemoryEvidence.memory_id == memory.id,
                MemoryEvidence.event_id == event.id,
                MemoryEvidence.stance == "supports",
            )
        ).first()
        if already is not None:
            continue

        session.add(
            MemoryEvidence(
                user_id=episode.user_id,
                memory_id=memory.id,
                event_id=event.id,
                episode_id=episode.id,
                stance="supports",
                weight=weight,
                excerpt=excerpt[:1000],
                observed_at=event.occurred_at,
            )
        )
        added += 1
    return added
