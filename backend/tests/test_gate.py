"""The consolidation gate: which episodes earn a model call.

Two questions are deliberately separate here. Whether a call is needed --
extraction cannot happen without one -- and what the summary text should be,
which is consolidation's decision.
"""

import pytest

from app.services.gate import (
    decide,
    fallback_title,
    has_named_entity,
    has_signal,
    is_noise,
)

# --- the verdicts -----------------------------------------------------------


def test_an_empty_episode_is_skipped():
    verdict, rationale = decide([])
    assert verdict == "skipped"
    assert "no dictation" in rationale


def test_an_all_noise_episode_is_skipped():
    verdict, _ = decide(["Testing testing.", "brb"])
    assert verdict == "skipped"


def test_a_decision_earns_a_call_however_short():
    """Extraction needs a model, so a nine-word decision costs a call."""
    verdict, _ = decide(["We decided ₹299 for the Pro tier."])
    assert verdict == "generated"


def test_small_talk_is_kept_verbatim_rather_than_paraphrased():
    verdict, rationale = decide(["Morning.", "All good here."])
    assert verdict == "verbatim"
    assert "nothing extractable" in rationale


def test_a_long_episode_with_nothing_extractable_is_skipped():
    """Too long to keep as written, and nothing worth a call."""
    texts = ["Just some rambling with no substance at all." for _ in range(6)]
    verdict, _ = decide(texts)
    assert verdict == "skipped"


def test_a_named_thing_earns_a_call_without_an_explicit_cue():
    """The cue list must not be the only way in, or unusual phrasing is lost."""
    verdict, _ = decide(["Priya is taking over the vendor contract."])
    assert verdict == "generated"


def test_filler_next_to_a_commitment_still_earns_a_call():
    verdict, _ = decide(["brb", "brb, I'll send the pricing after."])
    assert verdict == "generated"


def test_the_gate_makes_no_privacy_decision():
    """Sensitive dictations are refused at ingest, so none reach here."""
    assert "withheld" not in {decide(["We decided ₹299."])[0], decide([])[0]}




# --- the helpers ------------------------------------------------------------


def test_noise_is_judged_per_dictation_not_by_length():
    assert is_noise("brb")
    assert not is_noise("brb, I'll send the pricing after.")


def test_signal_cues_are_matched_case_insensitively():
    assert has_signal("We DECIDED on the price")


def test_a_sentence_initial_capital_is_not_a_named_entity():
    assert not has_named_entity("We settled the price today.")
    assert has_named_entity("We settled with ABC Inc today.")


# --- titles -----------------------------------------------------------------


def test_an_abbreviation_does_not_end_the_title():
    assert fallback_title("Dr. Roy approved the price.") == "Dr. Roy approved the price"


def test_the_earliest_terminator_wins():
    assert fallback_title("Did we ship? Yes. Ship it.") == "Did we ship?"


def test_an_initial_does_not_end_the_title():
    assert fallback_title("A. Sharma signed off.") == "A. Sharma signed off"


@pytest.mark.parametrize(
    ("dictation", "expected"),
    [
        ("So I was thinking, we should push Q3 pricing up 8%",
         "We should push Q3 pricing up 8%"),
        ("Just a note that Priya approved the vendor contract",
         "Priya approved the vendor contract"),
        ("Um, remind me to send the deck before Friday",
         "Remind me to send the deck before Friday"),
    ],
)
def test_speech_openers_are_stripped_from_the_title(dictation, expected):
    assert fallback_title(dictation) == expected


def test_an_opener_is_kept_when_nothing_useful_remains():
    """Stripping must not leave a title that says less than the filler did."""
    assert fallback_title("Okay done") == "Okay done"


def test_a_long_title_is_cut_at_a_word_boundary():
    text = "We agreed with the vendor that the renewal price for the enterprise tier moves to a different number"
    title = fallback_title(text)
    assert title.endswith("…")
    assert "  " not in title
    assert len(title) <= 91


def test_a_single_word_longer_than_the_limit_is_still_cut():
    assert fallback_title("x" * 200).endswith("…")


def test_an_empty_dictation_has_a_title():
    assert fallback_title("   ") == "Untitled"


# --- provider throttling ----------------------------------------------------


def test_the_client_spaces_calls_to_stay_under_a_quota(monkeypatch):
    """A worker claiming jobs as fast as it can will exhaust a free tier in
    seconds, so the spacing belongs at the single provider boundary."""
    import time

    from app.llm import client as client_module

    monkeypatch.setattr(client_module.settings, "llm_requests_per_minute", 600)
    monkeypatch.setattr(client_module.settings, "llm_api_key", "test-key")
    monkeypatch.setattr(client_module.genai, "Client", lambda **kwargs: object())

    llm = client_module.LLMClient()
    started = time.monotonic()
    llm._wait_for_slot()
    llm._wait_for_slot()
    assert time.monotonic() - started >= 0.09


def test_a_retry_delay_is_read_from_the_providers_reply():
    from app.llm.client import _RETRY_AFTER

    body = "{'retryDelay': '37.9s', 'status': 'RESOURCE_EXHAUSTED'}"
    assert float(_RETRY_AFTER.search(body).group(1)) == 37.9
