"""Structured output contract for episode consolidation.

Summary and claims come back from one call because deciding what mattered in
a stretch of activity is the same judgement for both. Splitting them would
pay the prompt twice and let the two halves disagree about what happened.

Field descriptions reach the model as part of the schema, so they are the
instructions.
"""

from pydantic import BaseModel, Field

from app.schemas.extraction import MemoryCandidateOut


class ConsolidationOut(BaseModel):
    title: str = Field(
        description=(
            "A short label for this stretch of activity, at most eight words. "
            "No trailing punctuation. If several unrelated things happened, "
            "name the most substantial one rather than listing them."
        )
    )
    summary: str = Field(
        description=(
            "Two to four sentences describing what the user said, in the "
            "third person. State decisions, commitments and facts plainly. "
            "Where the same subject came up in more than one place, say so "
            "once rather than repeating it. Do not speculate, do not add "
            "information that is not present, and do not editorialise."
        )
    )
    topic_tags: list[str] = Field(
        description=(
            "Between one and five short lowercase topic tags, such as "
            "'pricing' or 'launch date'. Nouns, not sentences."
        )
    )
    sensitive_indexes: list[int] = Field(
        default_factory=list,
        description=(
            "The numbers of any dictations that reveal someone's health, "
            "religion, politics, race or caste, sexual orientation, financial "
            "account details, or a password or key. Use the numbering shown. "
            "Judge the subject matter, not the tone: a difficult work "
            "conversation is not sensitive, and a mention of a hospital "
            "appointment for a meeting clash is not a health disclosure. "
            "Return an empty list when nothing qualifies, which is the usual "
            "answer."
        ),
    )
    memories: list[MemoryCandidateOut] = Field(
        description=(
            "Every durable claim worth remembering. Return an empty list if "
            "the dictations contain nothing durable -- small talk, testing, "
            "or logistics that will not matter tomorrow. Do not invent claims "
            "to fill the list. A claim may draw on dictations from different "
            "sittings when they are plainly about the same thing."
        )
    )
