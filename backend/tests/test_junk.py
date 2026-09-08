"""The ingest gate: what is not worth embedding, and what must survive it.

The bias is asymmetric on purpose. Embedding junk costs a noisier index;
dropping a real utterance means the user cannot find what they know they
said. So most of these tests assert that something is *kept*.
"""

from app.services.junk import (
    MIN_ASR_CONFIDENCE,
    RETRY_WINDOW_SECONDS,
    classify,
)

# --- what gets skipped ------------------------------------------------------


def test_a_discarded_dictation_is_not_embedded():
    """The user's own action beats every heuristic here."""
    assert (
        classify("Testing, testing.", committed_text="   ")
        == "discarded_by_user"
    )


def test_never_being_told_about_editing_is_not_evidence():
    """None means unknown, which is not the same as discarded."""
    assert classify("We decided ₹299 for the Pro tier.", committed_text=None) is None


def test_garbled_audio_is_not_embedded():
    reason = classify(
        "The the uh sorry the margin on.", asr_confidence=MIN_ASR_CONFIDENCE - 0.1
    )
    assert reason == "low_asr_confidence"


def test_confident_audio_is_embedded():
    assert (
        classify("Priya owns the vendor contract.", asr_confidence=0.95) is None
    )


def test_a_mic_test_is_not_embedded():
    assert classify("Testing testing one two three") == "no_content"


def test_empty_text_is_not_embedded():
    assert classify("   ") == "no_content"
    assert classify(None) == "no_content"


def test_eight_seconds_for_three_words_is_implausible():
    assert classify("um okay so", duration_ms=8000) == "implausible_timing"


def test_thirty_words_in_half_a_second_is_implausible():
    text = " ".join(["word"] * 30)
    assert classify(text, duration_ms=500) == "implausible_timing"


def test_a_normal_speaking_rate_is_embedded():
    text = "We agreed the Pro tier stays at ₹299 for Q3."
    assert classify(text, duration_ms=4000) is None


# --- retries ----------------------------------------------------------------


def test_the_same_sentence_seconds_later_is_a_retry():
    reason = classify(
        "Send the renewal quote to ABC Inc by Thursday.",
        previous_text="Send the renewal quote to ABC Inc by Thursday.",
        seconds_since_previous=12,
    )
    assert reason == "superseded_by_retry"


def test_punctuation_does_not_hide_a_retry():
    reason = classify(
        "send the renewal quote to abc inc by thursday",
        previous_text="Send the renewal quote to ABC Inc by Thursday.",
        seconds_since_previous=5,
    )
    assert reason == "superseded_by_retry"


def test_the_same_sentence_an_hour_later_is_not_a_retry():
    """Saying something again later is corroboration, not a failed take."""
    assert (
        classify(
            "Send the renewal quote to ABC Inc by Thursday.",
            previous_text="Send the renewal quote to ABC Inc by Thursday.",
            seconds_since_previous=RETRY_WINDOW_SECONDS + 1,
        )
        is None
    )


def test_out_of_order_events_do_not_produce_a_retry():
    """A negative gap means "the one before this" was never established."""
    assert (
        classify(
            "Send the renewal quote to ABC Inc by Thursday.",
            previous_text="Send the renewal quote to ABC Inc by Thursday.",
            seconds_since_previous=-30,
        )
        is None
    )


# --- what must survive ------------------------------------------------------


def test_filler_next_to_content_is_kept():
    """"brb" is filler; "brb, I'll send the pricing" is a commitment."""
    assert classify("brb, I'll send the pricing after.") is None


def test_a_four_character_dictation_is_kept():
    """Short is not empty. This is exactly what a user asks for later."""
    assert classify("₹299") is None


def test_a_single_substantive_word_is_kept():
    assert classify("Approved.") is None
