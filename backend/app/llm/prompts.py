"""Prompt text, kept together so it can be reviewed and diffed as source.

Summaries are the main thing semantic search matches against, so a summary
that adds detail the user never said becomes a citation to something that was
never dictated. Grounding is the priority over fluency.
"""

SUMMARISE_SYSTEM = """\
You summarise a person's own dictated notes and messages.

Rules:
- Use only what the dictations say. Never add facts, names, numbers or dates \
that are not present.
- Prefer the speaker's own words for decisions, amounts and names.
- If the dictations are trivial or contain nothing of substance, say so \
plainly rather than inventing significance.
- Write about the speaker in the third person ("they decided..."), never in \
the first person.
- Keep numbers, currencies and proper nouns exactly as written.
"""


def render_episode_for_summary(
    *,
    app: str | None,
    started_at: str,
    texts: list[str],
) -> str:
    """The user-side prompt: where the sitting happened, and what was said."""
    lines = [
        "A single sitting of dictation.",
        f"Application: {app or 'unknown'}",
        f"Started: {started_at}",
        "",
        "Dictations, in order:",
    ]
    lines.extend(f"{index}. {text}" for index, text in enumerate(texts, start=1))
    return "\n".join(lines)
