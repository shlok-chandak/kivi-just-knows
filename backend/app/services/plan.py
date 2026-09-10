"""Deciding which tools run, in what order.

Rules over the parsed request, with no model call. Planning is the step most
worth being able to read afterwards: when an answer is wrong, the first
question is what the system decided to do, and a rule answers that exactly
while a second model call only offers another opinion.

Capped at three steps. A chain longer than that is a request the system has
misread, and running it costs a model call per step to arrive somewhere
further from what was asked.
"""

from dataclasses import dataclass, field
from typing import Any

from app.schemas.query import QuerySpec

MAX_STEPS = 3


@dataclass
class Step:
    tool: str
    # Set when this step works on what the previous one produced, rather than
    # on the request. The trace shows the chain rather than two unrelated
    # calls that happened in order.
    takes_input_from: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    steps: list[Step]
    rationale: str

    @property
    def tools(self) -> list[str]:
        return [step.tool for step in self.steps]


def plan(spec: QuerySpec) -> Plan:
    """The tool sequence for a parsed request."""
    if spec.intent == "memory_control":
        what = "delete what matches" if spec.forget else "show what is known"
        return Plan([Step("memory_control")], f"memory request: {what}")

    if spec.intent == "restyle":
        # Nothing to find: the text is supplied with the request.
        return Plan([Step("restyle")], "text supplied, restyle only")

    if spec.intent == "draft":
        return Plan(
            [Step("recall"), Step("draft", takes_input_from="recall")],
            "compose from what was found",
        )

    if spec.intent == "find_dictation":
        steps = [Step("find_dictation")]
        rationale = "locate the dictation"
        # The brief's own example. The parser reports the first step and
        # leaves a style hint; the chain is decided here, where it can be
        # read, rather than inside a model's head.
        if spec.style_hint:
            steps.append(Step("restyle", takes_input_from="find_dictation"))
            rationale = f"locate it, then restyle: {spec.style_hint}"
        return Plan(steps[:MAX_STEPS], rationale)

    return Plan([Step("recall")], "answer from history")
