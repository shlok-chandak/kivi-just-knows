"""One way in for everything a person can ask.

Parse the request, plan the tools, run them. The plan is returned alongside
the result so what the system decided to do is visible next to what it did --
which is the first thing worth knowing when an answer is wrong.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.services import composer, finder, memory_control, parse, plan
from app.services import recall as recall_service
from app.services import restyle as restyle_service
from app.services import timeref

router = APIRouter(prefix="/ask", tags=["ask"])


class AskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=2000)
    # Text the person already has in front of them, for a restyle that acts
    # on a selection rather than on something retrieved.
    text: str | None = None


@router.post("")
def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    x_kivi_now: str | None = Header(default=None),
) -> dict:
    """Answer, find, restyle, draft, or edit memory -- whichever was asked."""
    now = _reference_now(x_kivi_now)
    user_id = settings.default_user_id

    spec, parsed = parse.parse(payload.request)
    when = timeref.resolve(spec.time_expression, now=now)
    made = plan.plan(spec)

    steps: list[dict] = []
    carried: str | None = payload.text

    for step in made.steps:
        result, carried = _run(
            db, user_id, step, spec, payload, when, now, carried
        )
        steps.append({"tool": step.tool, "result": result})

    db.commit()

    return {
        "request": payload.request,
        "understood_as": {
            "intent": spec.intent,
            "topic": spec.topic,
            "time": (
                {
                    "expression": when.expression,
                    "from": when.start.isoformat() if when.start else None,
                    "to": when.end.isoformat() if when.end else None,
                }
                if when
                else None
            ),
            "apps": spec.apps,
            "people": spec.people,
            "parsed_by_model": parsed,
        },
        "plan": {"tools": made.tools, "why": made.rationale},
        "steps": steps,
        "now": now.isoformat(),
    }


def _run(db, user_id, step, spec, payload, when, now, carried):
    """One step. Returns what to report and what to hand to the next step."""
    if step.tool == "recall":
        answer = recall_service.answer(
            db, user_id, spec.topic or payload.request,
            now=now,
            since=when.start if when else None,
            until=when.end if when else None,
            apps=spec.apps or None,
            person=spec.people[0] if spec.people else None,
        )
        return answer.as_dict(), answer.text

    if step.tool == "find_dictation":
        found = finder.find(
            db, user_id, now=now, when=when, apps=spec.apps or None,
            topic=spec.topic if spec.time_expression is None else "",
        )
        return (
            {
                "found": [item.as_dict() for item in found.found],
                "filters": found.filters,
                "widened": found.widened,
            },
            found.found[0].event.canonical_text if found.found else None,
        )

    if step.tool == "restyle":
        if not carried:
            return {"refused": "Nothing to restyle."}, None
        styled = restyle_service.restyle(
            db, user_id, carried, instruction=spec.style_hint, now=now
        )
        return styled.as_dict(), styled.text

    if step.tool == "draft":
        drafted = composer.compose(
            db, user_id, payload.request, topic=spec.topic,
            now=now,
            since=when.start if when else None,
            until=when.end if when else None,
            apps=spec.apps or None,
        )
        return drafted.as_dict(), drafted.text

    if step.tool == "memory_control":
        view = (
            memory_control.forget(db, user_id, topic=spec.topic, now=now)
            if spec.forget
            else memory_control.show(db, user_id, topic=spec.topic, now=now)
        )
        return view.as_dict(), None

    return {"refused": f"unknown tool: {step.tool}"}, None


def _reference_now(header: str | None) -> datetime:
    """The clock every time expression resolves against.

    Injectable because the corpus sits in the past: resolved against the wall
    clock, "yesterday" is an empty range and the system abstains on a
    question it could have answered.
    """
    if not header:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(header)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
