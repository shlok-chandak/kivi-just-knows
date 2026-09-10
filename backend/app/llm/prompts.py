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

You may be shown a KNOWN section listing beliefs already held, numbered K1, \
K2 and so on. It is context, not material. Never extract a claim from it, \
and never cite it -- claims cite dictations only.

Use it for two things.

To resolve what a dictation leaves out. People dictate in fragments and drop \
the subject once it is established, so "349 now" or "moved to Tuesday" only \
means something against what is already known. Write the resolved claim in \
full: given K1 "the Pro tier price is 299 a month", the dictation "349 now" \
is the claim "the Pro tier price is 349 a month". Resolve only what the \
known belief actually settles -- if nothing there tells you what a number \
refers to, leave the claim as the dictation supports it, or omit it.

To notice when something has changed. If a claim answers the same question \
as a known belief with a different answer, set replaces to that K number. \
This is how a decision is recorded as overturned rather than sitting \
alongside the one it replaced, both looking current. It applies when the \
wording shares nothing: "switching to Cashfree" replaces "going with \
Razorpay" because both name the payment provider. Do not set it because two \
things are on the same topic, and never because a known belief looks old.
"""


def render_episode_for_consolidation(
    *,
    started_at: str,
    sittings: list[list[Event]],
    known: list[str] | None = None,
) -> str:
    """The episode as its sittings, with dictations numbered across the whole.

    Numbering runs across the episode rather than restarting per sitting, so a
    claim citing dictation 7 is unambiguous. Sitting headers carry the app and
    the time, which is what lets the model tell a continuation from a move
    somewhere else -- structure that cost nothing to compute and would
    otherwise be thrown away before the expensive step.
    """
    lines: list[str] = []

    # Before the dictations, not after: what is already believed is what the
    # fragments have to be read against, and a reader who meets "349 now"
    # first has already decided what it means.
    if known:
        lines.append("KNOWN -- already believed. Context only; never cite.")
        lines.extend(
            f"K{position}. {text}" for position, text in enumerate(known, start=1)
        )
        lines.append("")

    lines += [
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
concluded, and MEMORY may be wrong where a DICTATION disagrees with it.

You may also be given standing context about the person -- how they like \
things written, what they are working on. Follow their stated preferences \
when writing the answer. It is context, never evidence: it cannot be cited, \
and it can never make a question answerable on its own.\
"""


def render_sources_for_answering(
    *, question: str, sources: list[str], profile: str = ""
) -> str:
    """The question and the numbered sources it must be answered from.

    Numbered rather than listed, because a citation has to point at something
    stable. The list is passed already ordered and already labelled by the
    caller: what counts as a source, and how it is described, is a retrieval
    decision rather than a prompt one.
    """
    lines: list[str] = []
    if profile:
        # Standing context, not evidence: it shapes how the answer is written
        # rather than what it may claim, so it is never citable.
        lines += ["About the person you are answering:", profile, ""]
    lines += [f"Question: {question}", "", "Sources:"]
    lines.extend(f"{index}. {text}" for index, text in enumerate(sources, start=1))
    if not sources:
        lines.append("(none found)")
    return "\n".join(lines)


PARSE_SYSTEM = """\
You read one request from a person about their own dictated notes, and \
return a structured description of what they are asking for. You do not \
answer it, and you do not have their notes in front of you.

Decide the intent from what they want to happen, not from the words used. \
"What did we decide about pricing" wants an answer, so recall. "Find the \
message I dictated at five" wants the text itself, so find_dictation. \
"Rewrite this more casually" acts on text they already have, so restyle. \
"Draft a reply to Priya" makes something new, so draft. "What do you know \
about me" and "forget the vendor thing" are memory_control.

Copy time words exactly as they were said and never turn them into a date. \
"Yesterday" stays "yesterday". Dates are worked out afterwards against a \
clock you cannot see, so a date from you would be wrong whenever that clock \
is not today.

Leave a field empty rather than filling it with a guess. An app nobody named \
and a person nobody mentioned both narrow the search onto nothing, and an \
empty field searches everywhere, which is recoverable.\
"""


def render_request_for_parsing(*, request: str) -> str:
    return f"Request: {request}"


RESTYLE_SYSTEM = """\
You rewrite a piece of the person's own dictated text so it reads the way \
they want it to read. You are not writing something new and you are not \
improving on what they meant.

Keep every fact, name, number, date and amount exactly as it appears. If the \
original says 349, the rewrite says 349. Losing or rounding one of these is \
the one failure that matters here, because the person will send this without \
checking it against the original.

Do not add. No greeting they did not write, no closing, no context they did \
not give, no softening of something they said plainly. If the original is \
three words, three words is a valid answer.

You are given their standing preferences about how they like things written. \
Follow them. Where the request asks for something that contradicts a standing \
preference, the request wins -- it is about this piece of text, and the \
preference is a default.\
"""


def render_text_for_restyling(
    *, text: str, instruction: str | None, preferences: str = ""
) -> str:
    lines = []
    if preferences:
        lines += ["How they like things written:", preferences, ""]
    if instruction:
        lines += [f"Asked for: {instruction}", ""]
    lines += ["Their text:", text]
    return "\n".join(lines)


DRAFT_SYSTEM = """\
You write a short piece of text for the person, using only the numbered \
sources from their own dictations. You are drafting on their behalf, so \
write it as they would send it, not as a report about them.

Every fact in the draft has to come from a source. If the sources do not \
cover something the draft needs, leave it out rather than filling it in -- \
a plausible invented detail is the failure that matters here, because they \
will send this without checking.

Keep numbers, names and dates exactly as the sources have them.

A source marked REPLACED is out of date. Never write it as current, and do \
not put it next to the current value as though both were true -- that is \
worse than leaving it out, because the reader cannot tell which one holds. \
Use the current value, and mention the old one only if the request is about \
the change itself.

Say what you drew on by source number. If the sources are too thin to write \
anything honest, say so in `answer` and leave the draft empty rather than \
writing something vague.

Follow their standing preferences about how they like things written.\
"""


def render_context_for_drafting(
    *, request: str, sources: list[str], preferences: str = ""
) -> str:
    lines = []
    if preferences:
        lines += ["How they like things written:", preferences, ""]
    lines += [f"Asked for: {request}", "", "From their own dictations:"]
    lines += [f"{i}. {t}" for i, t in enumerate(sources, start=1)]
    if not sources:
        lines.append("(nothing found)")
    return "\n".join(lines)
