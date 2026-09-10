"""The shape of a rewrite."""

from pydantic import BaseModel, Field


class RestyleOut(BaseModel):
    text: str = Field(
        description=(
            "The rewritten text, and nothing else. No preamble, no "
            "explanation, no quotation marks around it."
        )
    )
    changed: str = Field(
        description=(
            "One short clause naming what was changed -- 'dropped the "
            "greeting and tightened it to two lines'. This is shown to the "
            "person so they can see what happened to their words."
        )
    )
