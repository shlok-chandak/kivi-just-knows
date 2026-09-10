"""The shape of a drafted artifact."""

from pydantic import BaseModel, Field


class DraftOut(BaseModel):
    enough: bool = Field(
        description=(
            "True only if the sources actually support a draft. False when "
            "they are on the topic but too thin to write anything the person "
            "could send without correcting it."
        )
    )
    text: str = Field(
        default="",
        description=(
            "The draft, ready to send. Empty when enough is False."
        ),
    )
    note: str = Field(
        description=(
            "One sentence. When enough is False, what is missing. Otherwise, "
            "what the draft is based on."
        )
    )
    citations: list[int] = Field(
        default_factory=list,
        description="Source numbers the draft rests on. Empty when enough is False.",
    )
