"""Question in, grounded answer out.

The read path, and the first place the rest of the system is visible to a
person. Everything upstream -- refusing sensitive dictation, grouping into
episodes, forming beliefs, replacing them when they change -- exists to make
this step answerable.

Three things happen here and the order matters.

Retrieval narrows. Generation writes an answer from what was retrieved and
nothing else. Then the citations it claims are checked against what it was
actually given, because a citation nobody verified is a claim about a claim.

The model is allowed to decline. That is the point of the sufficiency
question rather than a fallback: search always returns its best matches, so
something is always available to write a fluent paragraph from, and a system
with no way to say "that is not in here" will confidently answer questions
it has no business answering.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.llm.client import SMALL, get_client
from app.llm.prompts import ANSWER_SYSTEM, render_sources_for_answering
from app.schemas.recall import AnswerOut
from app.services import profile as profile_service
from app.services import retrieval

logger = logging.getLogger("kivi.recall")

# How many retrieved items are shown to the model. Enough that a real answer
# is unlikely to fall outside it, small enough that the weakest matches do
# not pad the prompt with plausible-sounding near misses -- which is what an
# abstention decision has to see past.
CONTEXT_SIZE = 8


@dataclass
class Source:
    """One retrieved item as the model sees it, and as a citation resolves."""

    number: int
    kind: str
    id: uuid.UUID
    text: str
    occurred_at: datetime | None
    superseded: bool = False

    def rendered(self) -> str:
        """The line shown to the model.

        The label is part of the evidence. Whether something is the user's own
        wording or the system's conclusion changes how far it can be trusted,
        and a replaced belief that arrives unlabelled will be read as current.
        """
        parts = [self.kind.upper()]
        if self.superseded:
            parts.append("REPLACED")
        if self.occurred_at:
            parts.append(self.occurred_at.strftime("%d %b"))
        return f"[{', '.join(parts)}] {self.text}"


@dataclass
class Answer:
    """What was answered, from what, and what the answer could not use."""

    question: str
    answered: bool
    text: str
    citations: list[Source] = field(default_factory=list)
    superseded_note: str | None = None
    considered: list[Source] = field(default_factory=list)
    widened: list[str] = field(default_factory=list)
    filters_not_applied: list[str] = field(default_factory=list)
    called_model: bool = False
    usage: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answered": self.answered,
            "answer": self.text,
            "citations": [
                {
                    "number": source.number,
                    "kind": source.kind,
                    "id": str(source.id),
                    "text": source.text[:300],
                    "occurred_at": (
                        source.occurred_at.isoformat() if source.occurred_at else None
                    ),
                    "superseded": source.superseded,
                }
                for source in self.citations
            ],
            "superseded_note": self.superseded_note,
            "considered": len(self.considered),
            "widened": self.widened,
            "filters_not_applied": self.filters_not_applied,
        }


def _to_sources(candidates: Sequence[retrieval.Candidate]) -> list[Source]:
    return [
        Source(
            number=position,
            kind=candidate.kind,
            id=candidate.id,
            text=candidate.text,
            occurred_at=candidate.occurred_at,
            superseded=candidate.stale,
        )
        for position, candidate in enumerate(candidates, start=1)
    ]


def _resolve(numbers: Sequence[int], sources: Sequence[Source]) -> list[Source]:
    """Turn the model's citation numbers into the sources they name.

    Numbers outside the list are dropped rather than trusted. A citation to
    source 12 when eight were provided is not a source, and rendering it as
    one would put a reference in front of a reader with nothing behind it.
    """
    by_number = {source.number: source for source in sources}
    resolved: list[Source] = []
    for number in numbers:
        source = by_number.get(number)
        if source is None:
            logger.warning("dropping citation to source %s, which was not given", number)
            continue
        if source not in resolved:
            resolved.append(source)
    return resolved


def answer(
    session: Session,
    user_id: uuid.UUID,
    question: str,
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    apps: Sequence[str] | None = None,
    person: str | None = None,
) -> Answer:
    """Answer a question from what this user actually dictated."""
    now = now or datetime.now(timezone.utc)

    found = retrieval.search(
        session, user_id, question,
        now=now, since=since, until=until, apps=apps, person=person,
    )
    sources = _to_sources(found.candidates[:CONTEXT_SIZE])

    # Nothing retrieved is already an answer, and not one worth paying for.
    # Asking the model to decline over an empty list spends a call to be told
    # what the empty list said.
    if not sources:
        return Answer(
            question=question,
            answered=False,
            text="Nothing in your dictations covers that.",
            widened=found.widened,
            filters_not_applied=found.filters_not_applied,
        )

    resident = profile_service.load(session, user_id, now=now)

    completion = get_client().structured(
        prompt=render_sources_for_answering(
            question=question,
            sources=[source.rendered() for source in sources],
            profile=resident.rendered(),
        ),
        schema=AnswerOut,
        system=ANSWER_SYSTEM,
        tier=SMALL,
    )
    result: AnswerOut = completion.value

    citations = _resolve(result.citations, sources) if result.answered else []

    # An answer resting on nothing traceable is the failure this whole path
    # exists to avoid, so it is withdrawn rather than shown uncited.
    if result.answered and not citations:
        logger.warning("answer cited nothing usable, withdrawing: %r", question)
        return Answer(
            question=question,
            answered=False,
            text="Nothing in your dictations covers that.",
            considered=sources,
            widened=found.widened,
            filters_not_applied=found.filters_not_applied,
            called_model=True,
            usage=completion.usage,
        )

    _count_uses(session, citations)

    return Answer(
        question=question,
        answered=result.answered,
        text=result.answer,
        citations=citations,
        superseded_note=result.superseded_note,
        considered=sources,
        widened=found.widened,
        filters_not_applied=found.filters_not_applied,
        called_model=True,
        usage=completion.usage,
    )


def _count_uses(session: Session, citations: Sequence[Source]) -> None:
    """Record that a belief was actually used to answer something.

    Ranking gives a small boost to beliefs that keep proving useful, and this
    is the only place that signal is produced. Counting retrieval instead
    would reward being findable rather than being right, which is the thing
    the boost is meant to distinguish -- so only cited beliefs count, not
    everything that reached the prompt.
    """
    from app.models.memory import Memory

    for source in citations:
        if source.kind != "memory":
            continue
        memory = session.get(Memory, source.id)
        if memory is not None:
            memory.use_count = (memory.use_count or 0) + 1
    session.flush()
