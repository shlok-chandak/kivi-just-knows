"""Writing something new from what the person actually said.

The difference from recall is what happens to the sources. Recall reports
them; this turns them into something the person will send. That raises the
cost of an invented detail from a wrong answer to a wrong message in
somebody else's inbox, so the draft is allowed to come back empty.

Retrieval first, always. Composing before retrieving is writing fiction and
then looking for support.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.llm.client import SMALL, get_client
from app.llm.prompts import DRAFT_SYSTEM, render_context_for_drafting
from app.schemas.draft import DraftOut
from app.services import profile as profile_service
from app.services import recall as recall_service
from app.services import retrieval

logger = logging.getLogger("kivi.composer")

CONTEXT_SIZE = 8


@dataclass
class Draft:
    request: str
    enough: bool
    text: str
    note: str
    citations: list[recall_service.Source] = field(default_factory=list)
    considered: int = 0
    usage: Any = None

    def as_dict(self) -> dict:
        return {
            "request": self.request,
            "enough": self.enough,
            "text": self.text,
            "note": self.note,
            "citations": [
                {
                    "number": source.number,
                    "kind": source.kind,
                    "id": str(source.id),
                    "text": source.text[:300],
                }
                for source in self.citations
            ],
            "considered": self.considered,
        }


def compose(
    session: Session,
    user_id: uuid.UUID,
    request: str,
    *,
    topic: str = "",
    now: datetime | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    apps: Sequence[str] | None = None,
) -> Draft:
    """Draft something from this user's own dictations."""
    now = now or datetime.now(timezone.utc)

    found = retrieval.search(
        session, user_id, topic or request,
        now=now, since=since, until=until, apps=apps,
    )
    sources = recall_service.to_sources(found.candidates[:CONTEXT_SIZE])

    if not sources:
        return Draft(
            request=request,
            enough=False,
            text="",
            note="Nothing in your dictations covers this.",
        )

    resident = profile_service.load(session, user_id, now=now)

    completion = get_client().structured(
        prompt=render_context_for_drafting(
            request=request,
            sources=[source.rendered() for source in sources],
            preferences="\n".join(
                f"- {entry.memory.content}" for entry in resident.style
            ),
        ),
        schema=DraftOut,
        system=DRAFT_SYSTEM,
        tier=SMALL,
    )
    result: DraftOut = completion.value

    citations = (
        recall_service.resolve_citations(result.citations, sources) if result.enough else []
    )

    # A draft resting on nothing traceable is the failure this path exists to
    # avoid, so it is withdrawn rather than handed over uncited.
    if result.enough and not citations:
        logger.warning("draft cited nothing usable, withdrawing: %r", request[:80])
        return Draft(
            request=request,
            enough=False,
            text="",
            note="Could not tie a draft back to anything you actually said.",
            considered=len(sources),
            usage=completion.usage,
        )

    return Draft(
        request=request,
        enough=result.enough,
        text=result.text if result.enough else "",
        note=result.note,
        citations=citations,
        considered=len(sources),
        usage=completion.usage,
    )
