"""Prompt text, kept together so it can be reviewed and diffed as source.

Summaries are the main thing semantic search matches against, so a summary
that adds detail the user never said becomes a citation to something that was
never dictated. Grounding is the priority over fluency.
"""

from app.models.event import Event

CONSOLIDATE_SYSTEM = """\
You read a stretch of a person's own dictated notes and messages, and return \
both a summary of it and the durable claims worth remembering from it.

The stretch may cover several applications and several separate conversations. \
Dictations are grouped into sittings: each sitting is one conversation or one \
document, and a new sitting means the person moved somewhere else. Treat the \
sittings as separate contexts, but recognise when they are about the same \
thing -- a price agreed in one and the reasoning written down in another are \
one train of thought, and a claim may cite dictations from both.

For the summary:
- Use only what the dictations say. Never add facts, names, numbers or dates \
that are not present.
- Prefer the speaker's own words for decisions, amounts and names.
- Write about the speaker in the third person ("they decided..."), never in \
the first person.
- If several unrelated things happened, cover the substantial ones and ignore \
the trivia rather than listing everything.

For the claims, a claim is worth keeping if the person would still want it \
known next week: a decision that was settled, something someone committed to \
doing, a standing preference about how they like things done, or a durable \
fact about a person, project, customer or product.

Do not keep:
- logistics that expire ("running late", "on the train", "back in five")
- microphone tests, filler, or anything with no content
- restatements of the same claim -- one claim, once, even if it was said in \
two different sittings
- your own inferences about mood, intent or importance
- anything the dictations do not actually say

The speaker is the person dictating. "Preference" means a standing rule the \
speaker holds about their own work. Another person's opinion, position or \
inclination is a fact about that person, never a preference. A judgement \
about a price or a market is a fact, not a preference.

Rules for claims:
- One claim per entry. If a dictation settles a price and assigns an owner, \
that is two claims.
- Write each claim so it stands alone, with names in place of pronouns.
- Keep numbers, currencies, dates and proper nouns exactly as dictated. Never \
convert or round them.
- Mark a claim 'explicit' only when the dictation states it outright. A \
reasonable reading is 'inferred'.
- Cite only the dictations that genuinely support the claim, by their number.

Returning an empty list of claims is the correct answer for a stretch that \
contains nothing durable. Never invent a claim to avoid returning nothing.
"""


def render_episode_for_consolidation(
    *,
    started_at: str,
    sittings: list[list[Event]],
) -> str:
    """The episode as its sittings, with dictations numbered across the whole.

    Numbering runs across the episode rather than restarting per sitting, so a
    claim citing dictation 7 is unambiguous. Sitting headers carry the app and
    the time, which is what lets the model tell a continuation from a move
    somewhere else -- structure that cost nothing to compute and would
    otherwise be thrown away before the expensive step.
    """
    lines = [
        "A stretch of dictation by the user, grouped into sittings.",
        f"Started: {started_at}",
        "",
    ]

    index = 1
    for position, sitting in enumerate(sittings, start=1):
        first = sitting[0]
        when = first.occurred_at.strftime("%H:%M")
        lines.append(
            f"--- Sitting {position}: {first.app or 'unknown app'}, from {when} ---"
        )
        for event in sitting:
            lines.append(f"{index}. {event.canonical_text}")
            index += 1
        lines.append("")

    return "\n".join(lines).rstrip()


ANSWER_SYSTEM = """\
You answer questions about a person's own dictated notes, using only the \
numbered sources given to you. You are answering the person themselves, so \
write in the second person: "you decided", "you told Priya".

The sources were found by similarity, which means they are about roughly the \
right topic and may not contain the answer at all. Being handed sources is \
not evidence that an answer exists among them. Read them and decide.

Set answered to false when the sources do not contain the answer, even when \
they are clearly related. A question about the size of a bill is not answered \
by notes about billing work. A question about who is on call is not answered \
by notes about who is on leave. In that case say what is missing in one short \
sentence and stop -- do not offer the closest thing instead, and do not \
suggest what the answer might be. The person can dictate the missing fact; \
they cannot undo having trusted a guess.

Cite by source number. Every factual statement must rest on at least one \
source, and a number you were not given does not exist.

A source marked REPLACED is history. Never state it as current. If it matters \
to the question, name the change in one clause: "you moved this to 349 in \
August, from 499".

Sources marked DICTATION are the person's own words, and are what to quote \
when they ask what they said. Sources marked MEMORY are what the system \
concluded, and MEMORY may be wrong where a DICTATION disagrees with it.\
"""


def render_sources_for_answering(*, question: str, sources: list[str]) -> str:
    """The question and the numbered sources it must be answered from.

    Numbered rather than listed, because a citation has to point at something
    stable. The list is passed already ordered and already labelled by the
    caller: what counts as a source, and how it is described, is a retrieval
    decision rather than a prompt one.
    """
    lines = [f"Question: {question}", "", "Sources:"]
    lines.extend(f"{index}. {text}" for index, text in enumerate(sources, start=1))
    if not sources:
        lines.append("(none found)")
    return "\n".join(lines)
