"""Running one request end to end, with a trace of what happened.

Parse, plan, then run the planned tools in order, handing each one what the
last produced. Every stage writes a trace step saying what it decided and
why, so a wrong answer can be read back rather than guessed at.

This lives in a service rather than in the route because replay runs it too.
A second copy of the loop would drift from this one, and a replay that does
not do what the original did proves nothing.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services import composer, finder, memory_control, parse, plan
from app.services import recall as recall_service
from app.services import restyle as restyle_service
from app.services import timeref, tracing
from app.services.ablation import NONE, Ablation

logger = logging.getLogger("kivi.asking")


@dataclass
class Outcome:
    """What one request produced, and the trace that explains it."""

    request: str
    understood_as: dict[str, Any]
    plan: dict[str, Any]
    steps: list[dict[str, Any]] = field(default_factory=list)
    trace_id: uuid.UUID | None = None
    outcome: str = "answered"
    now: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "understood_as": self.understood_as,
            "plan": self.plan,
            "steps": self.steps,
            "trace_id": str(self.trace_id) if self.trace_id else None,
            "outcome": self.outcome,
            "now": self.now.isoformat() if self.now else None,
        }


def run(
    session: Session,
    user_id: uuid.UUID,
    request: str,
    *,
    text: str | None = None,
    now: datetime | None = None,
    cuts: Ablation = NONE,
    parsed: parse.Parsed | None = None,
) -> Outcome:
    """Answer, find, restyle, draft, or edit memory -- whichever was asked.

    `parsed` reuses an earlier reading of the same request. The ablations
    need it: they compare retrieval and generation, so holding the parse
    fixed across the arms isolates what is being measured, and it happens to
    save a model call per arm.
    """
    now = now or datetime.now(timezone.utc)

    with tracing.start_query_trace(
        session, user_id=user_id, request=request
    ) as recorder:
        parsed = parsed or parse.parse(request)
        spec = parsed.spec
        recorder.step(
            "parse",
            decision=f"{spec.intent}: {spec.topic[:60]}" if spec.topic else spec.intent,
            rationale=(
                "read by the model"
                if parsed.by_model
                else "the model call failed; treated as a plain question"
            ),
            usage=parsed.usage,
            output_summary=spec.model_dump(mode="json"),
        )

        when = timeref.resolve(spec.time_expression, now=now)
        made = plan.plan(spec)
        recorder.step(
            "plan",
            decision=" -> ".join(made.tools),
            rationale=made.rationale,
            input_summary={
                "time_expression": spec.time_expression,
                "resolved": _time_dict(when),
                # Recorded so a run with something withheld is never mistaken
                # for a normal one when the trace is read back.
                "withheld": cuts.as_dict() if cuts.active else None,
            },
        )

        steps: list[dict[str, Any]] = []
        carried: str | None = text
        last = "answered"

        for step in made.steps:
            if not cuts.allows(step.tool):
                recorder.step(step.tool, decision="skipped: disabled for this run")
                steps.append({"tool": step.tool, "result": {"skipped": True}})
                continue

            ran = _run(
                session, user_id, step.tool, spec, request, when, now, carried,
                cuts,
            )
            recorder.step(
                step.tool,
                decision=ran.decision,
                rationale=step.takes_input_from
                and f"working on what {step.takes_input_from} produced",
                usage=ran.usage,
                output_summary=_summarise(ran.result),
            )
            steps.append({"tool": step.tool, "result": ran.result})
            carried = ran.carried
            last = ran.outcome

        final = next(
            (s["result"].get("answer") or s["result"].get("text") for s in reversed(steps)
             if isinstance(s.get("result"), dict)),
            None,
        )
        recorder.close(last, final_output=final)

        return Outcome(
            request=request,
            understood_as={
                "intent": spec.intent,
                "topic": spec.topic,
                "time": _time_dict(when),
                "apps": spec.apps,
                "people": spec.people,
                "parsed_by_model": parsed.by_model,
            },
            plan={"tools": made.tools, "why": made.rationale},
            steps=steps,
            trace_id=recorder.id,
            outcome=last,
            now=now,
        )


@dataclass
class _Ran:
    """One tool's result, plus what the trace and the next step need."""

    result: dict
    carried: str | None = None
    decision: str = ""
    usage: Any = None
    outcome: str = "answered"


def _run(session, user_id, tool, spec, request, when, now, carried, cuts=NONE) -> _Ran:
    """One step of the plan."""
    if tool == "recall":
        answer = recall_service.answer(
            session, user_id, spec.topic or request,
            now=now,
            since=when.start if when else None,
            until=when.end if when else None,
            apps=spec.apps or None,
            person=spec.people[0] if spec.people else None,
            cuts=cuts,
        )
        return _Ran(
            result=answer.as_dict(),
            carried=answer.text,
            decision=(
                f"answered from {len(answer.citations)} sources"
                if answer.answered
                else "abstained: nothing found that answers this"
            ),
            usage=answer.usage,
            outcome="answered" if answer.answered else "abstained",
        )

    if tool == "find_dictation":
        found = finder.find(
            session, user_id, now=now, when=when, apps=spec.apps or None,
            topic=spec.topic if spec.time_expression is None else "",
        )
        return _Ran(
            result={
                "found": [item.as_dict() for item in found.found],
                "filters": found.filters,
                "widened": found.widened,
            },
            carried=found.found[0].event.canonical_text if found.found else None,
            decision=(
                f"found {len(found.found)}"
                + (f", widened: {', '.join(found.widened)}" if found.widened else "")
            ),
            outcome="answered" if found.found else "abstained",
        )

    if tool == "restyle":
        if not carried:
            return _Ran(
                result={"refused": "Nothing to restyle."},
                decision="refused: nothing to work on",
                outcome="partial",
            )
        styled = restyle_service.restyle(
            session, user_id, carried, instruction=spec.style_hint, now=now
        )
        return _Ran(
            result=styled.as_dict(),
            carried=styled.text,
            decision=(
                "rewrote it"
                if styled.trustworthy
                else f"kept the original: the rewrite dropped {', '.join(styled.dropped_facts)}"
            ),
            usage=styled.usage,
            outcome="answered" if styled.trustworthy else "partial",
        )

    if tool == "draft":
        drafted = composer.compose(
            session, user_id, request, topic=spec.topic,
            now=now,
            since=when.start if when else None,
            until=when.end if when else None,
            apps=spec.apps or None,
        )
        return _Ran(
            result=drafted.as_dict(),
            carried=drafted.text,
            decision=(
                f"drafted, {len(drafted.citations)} citations"
                if drafted.enough
                else "withdrew the draft: not enough to write from"
            ),
            usage=drafted.usage,
            outcome="answered" if drafted.enough else "abstained",
        )

    if tool == "memory_control":
        view = (
            memory_control.forget(session, user_id, topic=spec.topic, now=now)
            if spec.forget
            else memory_control.show(session, user_id, topic=spec.topic, now=now)
        )
        if view.refused:
            decision = f"refused: {view.refused}"
        elif spec.forget:
            decision = f"deleted {len(view.deleted)}"
        else:
            decision = f"showed {len(view.memories)}"
        return _Ran(
            result=view.as_dict(),
            decision=decision,
            outcome="partial" if view.refused else "answered",
        )

    return _Ran(
        result={"refused": f"unknown tool: {tool}"},
        decision=f"unknown tool: {tool}",
        outcome="error",
    )


def _time_dict(when) -> dict | None:
    if when is None:
        return None
    return {
        "expression": when.expression,
        "from": when.start.isoformat() if when.start else None,
        "to": when.end.isoformat() if when.end else None,
    }


def _summarise(result: dict) -> dict:
    """A trace-sized version of a tool result.

    Full results hold whole dictations and drafts. Storing them again on the
    trace would duplicate the corpus row by row for no added explanation.
    """
    keep = ("answered", "enough", "refused", "widened", "filters", "deleted")
    small = {key: result[key] for key in keep if key in result}
    for key in ("citations", "found", "memories"):
        if key in result:
            small[f"{key}_count"] = len(result[key])
    return small
