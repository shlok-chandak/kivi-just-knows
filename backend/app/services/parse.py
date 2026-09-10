"""Turning a request into a QuerySpec.

The only model call on the way in. It decides what was asked; code decides
what to do about it, which keeps planning inspectable and the model's job
small enough to be reliable.

A failure here falls back to recall over the raw text rather than erroring.
Answering the question as if it were a plain one is nearly always closer to
what the person wanted than a stack trace.
"""

import logging

from app.llm.client import SMALL, get_client
from app.llm.prompts import PARSE_SYSTEM, render_request_for_parsing
from app.schemas.query import QuerySpec

logger = logging.getLogger("kivi.parse")


def parse(request: str) -> tuple[QuerySpec, bool]:
    """The request as a QuerySpec, and whether the model produced it."""
    try:
        completion = get_client().structured(
            prompt=render_request_for_parsing(request=request),
            schema=QuerySpec,
            system=PARSE_SYSTEM,
            tier=SMALL,
        )
    except Exception:  # noqa: BLE001 - a parse failure must not lose the question
        logger.exception("parse failed, falling back to recall: %r", request[:80])
        return QuerySpec(intent="recall", topic=request), False

    spec: QuerySpec = completion.value

    # An empty topic makes every search return nothing. The request itself is
    # a worse search string than a clean topic and a much better one than "".
    if not spec.topic.strip() and spec.intent != "memory_control":
        spec.topic = request

    return spec, True
