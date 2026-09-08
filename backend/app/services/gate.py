"""Deciding whether an episode is worth a model call.

Structure is free and runs on everything. Inference is earned. Most episodes
never reach a model: a single dictation is its own best summary, and a stretch
of logistics holds nothing durable to extract.

Privacy is not decided here. Sensitive dictations are refused at ingest and
never stored, so by the time an episode exists there is nothing to withhold.
"""

import re

# Two short dictations are cheaper to keep verbatim than to paraphrase.
VERBATIM_PAIR_MAX_CHARS = 200

# Titles longer than this are cut at a word boundary rather than mid-phrase.
TITLE_MAX_CHARS = 90


# --- deciding what to do ----------------------------------------------------

# Cues that something was settled, promised, or holds as a standing rule.
# Deliberately plain string matching: this decides whether to spend a call,
# not what the claim is.
SIGNAL_CUES = (
    # something was settled
    "decide", "agree", "settle", "final", "go with", "going with", "chose",
    "choose", "approved", "signed off", "confirm", "priced", "pricing",
    # someone committed to something
    "will ", "i'll", "we'll", "let's", "lets ", "owns", "owner", "assign",
    "deadline", "due ", "commit", "promise", "tomorrow",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    # a standing preference
    "always", "never", "prefer", "from now on", "make sure", "remember to",
    # something changed
    "moving", "moved", "changed to", "instead of", "slips",
)

NOISE_PATTERNS = (
    r"^\s*(testing|test)\b",
    r"\bbrb\b",
    r"\bone two three\b",
    r"^\s*(hello|hi|hey)[\s.!?]*$",
    r"\bmic check\b",
)


def has_signal(text: str) -> bool:
    lowered = text.lower()
    return any(cue in lowered for cue in SIGNAL_CUES)


# Words that are capitalised because a sentence started, not because they
# name anything. Skipping the first token instead would miss the commonest
# shape a dictation takes -- "Priya is taking over the vendor contract" --
# where the subject is exactly the word that opens the sentence.
_SENTENCE_OPENERS = frozenset(
    {
        # greetings and courtesies, which open a dictation as often as a
        # pronoun does and name nothing
        "morning", "afternoon", "evening", "hello", "hey", "hi", "thanks",
        "thank", "sorry", "sure", "great", "good", "fine", "done", "nothing",
        "maybe", "all", "everything", "everyone",
        "a", "also", "and", "any", "are", "as", "ask", "at", "be", "before",
        "but", "can", "could", "did", "do", "does", "for", "from", "get",
        "going", "had", "has", "have", "he", "her", "his", "how", "i", "if",
        "in", "is", "it", "its", "just", "keep", "let", "lets", "make", "may",
        "might", "must", "my", "need", "no", "not", "now", "of", "okay", "on",
        "once", "one", "only", "or", "our", "please", "remind", "send", "set",
        "she", "should", "so", "some", "still", "such", "tell", "than", "that",
        "the", "their", "them", "then", "there", "these", "they", "this",
        "those", "to", "up", "use", "very", "was", "we", "well", "were",
        "what", "when", "where", "which", "while", "who", "why", "will",
        "with", "would", "yes", "yeah", "you", "your",
    }
)


def has_named_entity(text: str) -> bool:
    """Rough proper-noun check: a capitalised word that is not ordinary English.

    A heuristic, not recognition. Real entity extraction happens later; this
    only decides whether the episode is worth a call, so it errs towards
    saying yes -- a false positive costs one call, a false negative loses a
    memory permanently.
    """
    for token in text.split():
        stripped = token.strip("\"'([{.,;:!?)]}")
        if len(stripped) <= 1 or not stripped[0].isupper():
            continue
        if stripped.lower() in _SENTENCE_OPENERS:
            continue
        return True
    return False


def is_noise(text: str) -> bool:
    """Whether one dictation is nothing but filler.

    Judged per dictation, because the patterns describe a whole utterance:
    "brb" is filler, "brb, I'll send the pricing after" is a commitment that
    happens to open with filler. Anything carrying a signal is not noise, no
    matter what else it contains -- length is a poor proxy for substance.
    """
    stripped = text.strip()
    if has_signal(stripped):
        return False
    return any(
        re.search(pattern, stripped, flags=re.IGNORECASE)
        for pattern in NOISE_PATTERNS
    )


def decide(texts: list[str]) -> tuple[str, str]:
    """How this episode should be handled, and why.

    Returns one of 'skipped', 'verbatim' or 'generated' with a plain-language
    rationale for the trace. Most certain first, then cost.
    """
    if not texts:
        return "skipped", "no dictation in this episode carries text"

    combined = " ".join(texts)

    if all(is_noise(text) for text in texts):
        return "skipped", f"noise, nothing durable said: {combined[:60]!r}"

    # Nothing settled, promised or named means nothing durable to extract, so
    # there is nothing a call could return.
    extractable = has_signal(combined) or has_named_entity(combined)
    if not extractable:
        # Short enough to keep as it stands; no paraphrase is worth a call.
        if len(texts) <= 2 and len(combined) < VERBATIM_PAIR_MAX_CHARS:
            return (
                "verbatim",
                f"{len(texts)} short dictation(s), nothing extractable: kept "
                "as written rather than paraphrased",
            )
        return "skipped", "nothing decided, promised or preferred, and nothing named"

    # Extraction cannot be done without a model, so an episode carrying
    # anything durable earns the call whatever its length. The summary text is
    # still chosen separately -- a single raw dictation is a better embedding
    # target than a paraphrase of it.
    return (
        "generated",
        f"{len(texts)} dictations, {len(combined)} chars: summarised and "
        "extracted in one call",
    )


# --- titles for the branches that skip the model ----------------------------

# A sentence may end at .?! followed by a space and a capital or digit.
_SENTENCE_END = re.compile(r"[.!?]\s+(?=[\"'(]?[A-Z0-9])")

# Words whose trailing full stop is part of the word, not a sentence ending.
_ABBREVIATIONS = frozenset(
    {
        "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc",
        "e.g", "i.e", "no", "approx", "inc", "ltd", "co", "fig", "vol",
    }
)

# Openers that speech carries and a title does not need.
_OPENERS = (
    "so", "okay", "ok", "um", "uh", "erm", "well", "right", "yeah", "yep",
    "just a note that", "just noting that", "i was thinking", "i think",
    "note that", "reminder that", "quick note",
)

MIN_WORDS_AFTER_STRIP = 3


def _first_sentence(text: str) -> str:
    """The first sentence, without being fooled by abbreviations.

    "Dr. Roy approved the price." is one sentence, not "Dr". Single letters
    are skipped too, so an initial like "A. Sharma" stays intact.
    """
    for match in _SENTENCE_END.finditer(text):
        head = text[: match.start()]
        last_word = head.rsplit(" ", 1)[-1].strip("\"'([{").lower()
        if len(last_word) <= 1 or last_word in _ABBREVIATIONS:
            continue
        return text[: match.start() + 1]
    return text


def _strip_openers(text: str) -> str:
    """Drop leading filler, while what remains still stands on its own."""
    working = text
    changed = True
    while changed:
        changed = False
        lowered = working.lower()
        for opener in _OPENERS:
            if not lowered.startswith(opener):
                continue
            remainder = working[len(opener) :].lstrip(" ,-–—:")
            if len(remainder.split()) < MIN_WORDS_AFTER_STRIP:
                continue
            working = remainder
            changed = True
            break
    return working[:1].upper() + working[1:] if working else text


def fallback_title(text: str) -> str:
    """Title for an episode that skips the model: the sentence itself.

    A dictation is usually one short sentence, which makes a better title than
    any fixed-word truncation of it -- and a better one than a handle like
    "pricing", which would not distinguish it from every other pricing
    episode.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Untitled"

    cleaned = _strip_openers(_first_sentence(cleaned))

    if len(cleaned) > TITLE_MAX_CHARS:
        head = cleaned[:TITLE_MAX_CHARS]
        # Cut at a word boundary, unless the first word is longer than the
        # limit, in which case cutting mid-word is the only option left.
        if " " in head:
            head = head.rsplit(" ", 1)[0]
        else:
            head = head[: TITLE_MAX_CHARS - 1]
        cleaned = head + "…"

    return cleaned.rstrip(".,;:!") or "Untitled"
