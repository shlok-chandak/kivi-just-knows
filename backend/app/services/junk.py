"""The ingest gate: which dictations are not worth embedding.

Every event is stored regardless. Only the embedding is withheld, and only
when a signal says the text is not language the user meant to produce.

The asymmetry is deliberate. Embedding junk costs a slightly noisier index;
dropping something real means the user cannot find what they know they said,
with no way to recover it. So the question is never "is this useful?" -- that
cannot be known at ingest -- but "is this definitely not a real utterance?"
"""

import re

# Below this, the recogniser is guessing and the text would poison the index.
MIN_ASR_CONFIDENCE = 0.55

# Plausible speaking rates. Outside this range the duration and the text
# disagree, which means one of them is wrong.
MIN_WORDS_PER_SECOND = 0.4
MAX_WORDS_PER_SECOND = 8.0

# Long enough to be a real pause, short enough that a repeat is a retry.
RETRY_WINDOW_SECONDS = 45.0

# Words that carry no content on their own. A dictation is only rejected when
# it consists of nothing else -- "brb, I'll send the pricing after" stays,
# because "pricing" is not in here.
_FILLER = frozenset(
    {
        "test", "testing", "tests", "check", "checking", "mic", "hello", "hey",
        "hi", "yo", "um", "uh", "erm", "hmm", "mm", "ah", "oh", "okay", "ok",
        "brb", "yeah", "yep", "nope", "no", "yes", "sorry", "wait", "nothing",
        "one", "two", "three", "four", "five",
    }
)

_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _is_filler_only(text: str) -> bool:
    """True when every word is filler, so nothing is left to remember."""
    words = _words(text)
    if not words:
        return True
    return all(word in _FILLER for word in words)


def _normalise(text: str) -> str:
    """Comparable form, so punctuation differences do not hide a repeat."""
    return " ".join(_words(text))


def classify(
    text: str | None,
    *,
    committed_text: str | None = None,
    asr_confidence: float | None = None,
    duration_ms: int | None = None,
    previous_text: str | None = None,
    seconds_since_previous: float | None = None,
) -> str | None:
    """Why this dictation should not be embedded, or None to embed it.

    Pure: no clock, no database. Ordered cheapest and most certain first.
    """
    # The user's own action beats every heuristic here. An empty committed
    # text means they discarded what was produced; None means we were never
    # told either way, which is not evidence of anything.
    if committed_text is not None and not committed_text.strip():
        return "discarded_by_user"

    if not text or not text.strip():
        return "no_content"

    if asr_confidence is not None and asr_confidence < MIN_ASR_CONFIDENCE:
        return "low_asr_confidence"

    if duration_ms is not None and duration_ms > 0:
        rate = len(_words(text)) / (duration_ms / 1000.0)
        if rate < MIN_WORDS_PER_SECOND or rate > MAX_WORDS_PER_SECOND:
            return "implausible_timing"

    if _is_filler_only(text):
        return "no_content"

    # A negative gap means the events were not handed to us in order, in which
    # case "the one before this" is not established and no retry is claimed.
    if (
        previous_text
        and seconds_since_previous is not None
        and 0 <= seconds_since_previous <= RETRY_WINDOW_SECONDS
        and _normalise(text) == _normalise(previous_text)
    ):
        return "superseded_by_retry"

    return None
