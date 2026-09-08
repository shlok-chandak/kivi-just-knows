"""The shape of one extracted claim.

Returned as part of consolidation (see app.schemas.consolidation), which asks
for a summary and the claims together in one call.

Field descriptions reach the model as part of the schema, so they are the
instructions. Two fields carry most of the weight:

`source_indexes` ties every claim to the dictations that produced it, which is
what makes provenance real rather than asserted. `basis` separates what the
user actually said from what was inferred on their behalf -- an explicit
statement is believed at once, an inference has to earn it.
"""

from typing import Literal

from pydantic import BaseModel, Field

MemoryType = Literal["fact", "decision", "commitment", "preference"]
Basis = Literal["explicit", "inferred"]


class MemoryCandidateOut(BaseModel):
    content: str = Field(
        description=(
            "The claim, as one short standalone sentence in the third person. "
            "It must make sense on its own, with no pronouns referring outside "
            "itself: write 'Aditya owns the pricing page' rather than 'he owns "
            "it'. Keep numbers, currencies and names exactly as dictated."
        )
    )
    type: MemoryType = Field(
        description=(
            "'decision' for a choice that was settled. "
            "'commitment' for something a named person will do. "
            "'preference' ONLY for a standing rule the speaker holds about how "
            "their own work should be done -- 'I always want release notes as "
            "bullet points'. Someone else's opinion or position is NOT a "
            "preference, it is a fact about that person. A view about a market "
            "or a price is not a preference either. "
            "'fact' for anything else durable."
        )
    )
    basis: Basis = Field(
        description=(
            "'explicit' only when the dictation states this almost word for "
            "word, so the claim is a restatement. Use 'inferred' for anything "
            "requiring interpretation -- combining two dictations, reading "
            "intent, or attributing a belief, preference or opinion to another "
            "person. Attributing a mental state to anyone other than the "
            "speaker is always inferred. If in doubt, say inferred."
        )
    )
    subject: str | None = Field(
        default=None,
        description=(
            "The person, project, company or product this claim is mainly "
            "about, named exactly as it appears in the dictation. Null if the "
            "claim is not about a particular thing."
        ),
    )
    source_indexes: list[int] = Field(
        description=(
            "The numbers of the dictations this claim comes from, using the "
            "numbering shown. At least one. Include only dictations that "
            "genuinely support the claim."
        )
    )
    excerpt: str = Field(
        description=(
            "The words from those dictations that produced this claim, quoted "
            "verbatim. Do not paraphrase and do not add words."
        )
    )
