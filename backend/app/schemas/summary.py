"""Structured output contract for episode summarisation.

Field descriptions are sent to the model as part of the schema, so they are
instructions, not just documentation.
"""

from pydantic import BaseModel, Field


class EpisodeSummaryOut(BaseModel):
    title: str = Field(
        description=(
            "A short label for this sitting, at most eight words. "
            "No trailing punctuation."
        )
    )
    summary: str = Field(
        description=(
            "Two to four sentences describing what the user said, in the "
            "third person. State decisions, commitments and facts plainly. "
            "Do not speculate, do not add information that is not present, "
            "and do not editorialise."
        )
    )
    topic_tags: list[str] = Field(
        description=(
            "Between one and five short lowercase topic tags, such as "
            "'pricing' or 'launch date'. Nouns, not sentences."
        )
    )
