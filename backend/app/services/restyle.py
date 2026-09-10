"""Rewriting the person's own text in their voice.

The style comes from the resident profile -- the same preferences that ride
along with every answer. That is deliberate: a preference the person can see
and edit is a better account of their voice than one induced silently, and
it means "how do I like release notes" and "write this like I would" are
answered from the same place.

The check afterwards is the part that matters. A rewrite that quietly drops
a number is worse than no rewrite, because the person will send it without
comparing it against the original.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.llm.client import SMALL, get_client
from app.llm.prompts import RESTYLE_SYSTEM, render_text_for_restyling
from app.schemas.restyle import RestyleOut
from app.services import profile as profile_service

logger = logging.getLogger("kivi.restyle")

# Numbers, money and dates: the things a rewrite must carry through intact.
_FACTS = re.compile(
    r"[\d]+(?:[.,]\d+)*%?"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b",
    re.IGNORECASE,
)


@dataclass
class Restyled:
    original: str
    text: str
    changed: str
    preferences: list[str] = field(default_factory=list)
    dropped_facts: list[str] = field(default_factory=list)
    usage: Any = None

    @property
    def trustworthy(self) -> bool:
        return not self.dropped_facts

    def as_dict(self) -> dict:
        return {
            "original": self.original,
            "text": self.text,
            "changed": self.changed,
            "styled_by": self.preferences,
            "dropped_facts": self.dropped_facts,
            "trustworthy": self.trustworthy,
        }


def facts_in(text: str) -> set[str]:
    return {match.group(0).lower() for match in _FACTS.finditer(text)}


def restyle(
    session: Session,
    user_id: uuid.UUID,
    text: str,
    *,
    instruction: str | None = None,
    now: datetime | None = None,
) -> Restyled:
    """Rewrite `text`, keeping every fact in it."""
    resident = profile_service.load(session, user_id, now=now)
    preferences = [entry.memory.content for entry in resident.style]

    completion = get_client().structured(
        prompt=render_text_for_restyling(
            text=text,
            instruction=instruction,
            preferences="\n".join(f"- {line}" for line in preferences),
        ),
        schema=RestyleOut,
        system=RESTYLE_SYSTEM,
        tier=SMALL,
    )
    result: RestyleOut = completion.value

    # Checked rather than trusted. The person is about to send this.
    dropped = sorted(facts_in(text) - facts_in(result.text))
    if dropped:
        logger.warning("restyle dropped facts %s, returning the original", dropped)

    return Restyled(
        original=text,
        # A rewrite missing a number is not a worse rewrite, it is a wrong
        # one. Hand back the original and say why.
        text=text if dropped else result.text,
        changed=(
            f"kept the original: the rewrite dropped {', '.join(dropped)}"
            if dropped
            else result.changed
        ),
        preferences=preferences,
        dropped_facts=dropped,
        usage=completion.usage,
    )
