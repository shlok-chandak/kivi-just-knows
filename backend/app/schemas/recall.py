"""The shape of an answer.

Field descriptions reach the model as part of the schema, so they are the
instructions rather than documentation of them.

Two fields do the work that separates this from a chatbot over a database.

`citations` ties every claim in the answer to the numbered source it came
from. An answer nobody can check is worth less than no answer, because the
reader has no way to tell the two apart.

`answered` is permission to say nothing. A model asked a question will
produce a fluent reply from whatever it was handed, and the retrieved set
always contains something -- so without an explicit way to decline, the
failure mode is not silence, it is confident invention.
"""

from pydantic import BaseModel, Field


class AnswerOut(BaseModel):
    answered: bool = Field(
        description=(
            "True only if the sources actually contain the answer. False if "
            "they are merely on the same topic. Sources mentioning billing do "
            "not answer a question about the size of a bill, and sources about "
            "who is away do not answer who is on call. Guessing from a related "
            "source is the worst outcome available: the reader cannot tell it "
            "from a real answer. When unsure, answer False."
        )
    )
    answer: str = Field(
        description=(
            "The answer in one or two short sentences, in the second person "
            "('you decided...'). State it plainly, without hedging or "
            "restating the question. If answered is False, say briefly what is "
            "missing -- 'nothing here says what the AWS bill is' -- and do not "
            "offer the nearest related fact as a substitute."
        )
    )
    citations: list[int] = Field(
        default_factory=list,
        description=(
            "The numbers of the sources this answer rests on, from the list "
            "provided. Every factual statement must trace to at least one. "
            "Empty when answered is False."
        ),
    )
    superseded_note: str | None = Field(
        default=None,
        description=(
            "If a source is marked REPLACED and is relevant, one short clause "
            "naming what changed: 'this was 499 until August'. Null otherwise. "
            "Never present a replaced value as current."
        ),
    )
