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

logger = logging.getLogger("kivi.claims")

# Numbers, money, dates and times. Two claims differing in any of these
# contradict each other, however alike the sentences read -- which is what
# keeps a price change from being absorbed into the price it replaced.
_VALUES = re.compile(r"[\d]+(?:[.,]\d+)*%?|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b")


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def values_in(text: str) -> frozenset[str]:
    """The numbers and dates a claim commits to."""
    return frozenset(match.group(0).lower() for match in _VALUES.finditer(text))


def claim_key(subject: str | None, content: str) -> str:
    """The identity of a claim, as far as this build can determine it.

    Deliberately not scoped to an episode or a conversation. Scoping identity
    to where a claim was said means the same statement made in two places
    becomes two beliefs that can never corroborate each other, which is the
    opposite of what a memory system is for.
    """
    material = f"{_normalise(subject or '')}|{_normalise(content)}"
    return hashlib.sha256(material.encode()).hexdigest()[:32]


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
) -> str:
    """Store one claim with its evidence.

    Returns what happened: "created", "reinforced", "superseded", or
    "refused". The caller reports these separately, because a run that
    reinforced ten beliefs did not learn ten new things.
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

    existing = session.scalars(
        select(Memory).where(
            Memory.user_id == episode.user_id,
            Memory.claim_key == key,
            Memory.status != "superseded",
        )
    ).first()

    outcome = "reinforced"
    if existing is None:
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
