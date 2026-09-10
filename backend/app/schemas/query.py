"""What a request turns into before anything acts on it.

One model call produces this and nothing else. Everything downstream --
which tools run, what SQL narrows by, whether to abstain -- is rules over
this object, so the model decides what was asked and code decides what to do
about it.

The field that matters most is `time_expression`. It holds the user's own
words, never a date. Dates are arithmetic against an injectable clock, and a
model that emits them produces something plausible and wrong at a month
boundary, which nothing downstream can detect.
"""

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["recall", "find_dictation", "restyle", "draft", "memory_control"]
Artifact = Literal["message", "prompt", "doc", "none"]


class QuerySpec(BaseModel):
    intent: Intent = Field(
        description=(
            "What the person wants done. "
            "'recall' to answer a question from history. "
            "'find_dictation' to locate a specific thing they said -- they are "
            "asking for the text itself, usually with a time or an app. "
            "'restyle' to rewrite text that already exists. "
            "'draft' to compose something new. "
            "'memory_control' to see or delete what is remembered. "
            "When a request does both -- find something and then polish it -- "
            "use the first step, 'find_dictation'; the rest is planned in code."
        )
    )
    topic: str = Field(
        description=(
            "What the request is about, as search text. Strip the instruction "
            "and keep the subject: for 'what did we decide about pricing', "
            "the topic is 'pricing decision'. Empty when the request names no "
            "subject, as in 'what do you know about me'."
        )
    )
    time_expression: str | None = Field(
        default=None,
        description=(
            "The words used about time, copied as spoken: 'yesterday', "
            "'around 5 PM yesterday', 'last Tuesday', 'last week'. Never a "
            "date, and never invented -- null when the request says nothing "
            "about time."
        ),
    )
    apps: list[str] = Field(
        default_factory=list,
        description=(
            "Applications named in the request: slack, gmail, whatsapp, "
            "notion. Empty when none is named."
        ),
    )
    people: list[str] = Field(
        default_factory=list,
        description=(
            "People named, exactly as written. These are search terms, not "
            "filters -- who a message was sent to is not recorded."
        ),
    )
    memory_types: list[str] = Field(
        default_factory=list,
        description=(
            "Restrict to these kinds of memory when the request clearly asks "
            "for one: decision, commitment, preference, fact. Usually empty."
        ),
    )
    exhaustive: bool = Field(
        default=False,
        description=(
            "True when the request asks for everything on a subject -- "
            "'everything about', 'all of', 'summarise the whole'. Those need a "
            "scan in time order; top matches alone lose the long tail of a "
            "thread that ran for weeks."
        ),
    )
    artifact: Artifact = Field(
        default="none",
        description=(
            "What to produce, when the request asks for something written: a "
            "message, a prompt, a document. 'none' for a question."
        ),
    )
    style_hint: str | None = Field(
        default=None,
        description=(
            "How the person asked for something to be written -- 'casual', "
            "'for the meeting I am walking into', 'more formal'. "
            "Fill this whenever the request asks for text to be rewritten, "
            "polished, tightened or adjusted, INCLUDING when the main intent "
            "is find_dictation: 'find the 5pm message and polish it for the "
            "meeting' is a find whose result is then restyled, and this field "
            "is the only thing that says so. Null when the request asks for "
            "nothing to be rewritten."
        ),
    )
    forget: bool = Field(
        default=False,
        description=(
            "True only when the request asks for something to be deleted or "
            "forgotten. Never true for a question about what is remembered."
        ),
    )
