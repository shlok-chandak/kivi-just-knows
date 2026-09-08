"""Sensitive detection, which refuses a dictation outright.

Refusal is unrecoverable and silent about its contents, so the false-positive
tests below matter more than the true-positive ones. A missed category costs
privacy; a wrong match costs the user something they will never see again.
"""

import pytest

from app.services.sensitive import DETECTION_IMPLEMENTED, detect_sensitive


def test_detection_is_implemented():
    """The inert-policy warning must be off once these rules exist."""
    assert DETECTION_IMPLEMENTED is True


# --- structural: a shape, not a subject -------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "My staging database password is correct-horse-battery-staple.",
        "The API key is sk_live_4eC39HqLyjWDarjtT1zdp7dc.",
        "Set the access token to ghp_16C7e42F292c6912E7710c838347Ae178B4a.",
        "OTP is 448211, use it before it expires.",
    ],
)
def test_credentials_are_refused(text):
    assert detect_sensitive(text) == "credentials"


@pytest.mark.parametrize(
    "text",
    [
        "Card number 4111 1111 1111 1111, expiry next March.",
        "The CVV is on the back, I'll read it out.",
        "IFSC code for the vendor account is HDFC0001234.",
        "My PAN is ABCDE1234F.",
    ],
)
def test_financial_details_are_refused(text):
    assert detect_sensitive(text) == "financial_account"


# --- lexical: two signals must agree ----------------------------------------


def test_a_health_disclosure_is_refused():
    assert detect_sensitive("My biopsy results came back clear.") == "health"


def test_a_religious_disclosure_is_refused():
    assert detect_sensitive("My family is devout Hindu.") == "religion"


def test_a_political_disclosure_is_refused():
    assert detect_sensitive("I voted for the BJP in my constituency.") == "politics"


def test_a_caste_disclosure_is_refused():
    reason = detect_sensitive("His family belongs to a scheduled caste.")
    assert reason == "race_ethnicity"


def test_an_orientation_disclosure_is_refused():
    reason = detect_sensitive("My brother came out to us last year.")
    assert reason == "sexual_orientation"


# --- false positives, which cost a dictation forever ------------------------


@pytest.mark.parametrize(
    "text",
    [
        # A topic word with no personal context.
        "The launch metrics look healthy this quarter.",
        "The political situation with the vendor is delaying the contract.",
        "We need a diagnosis of why the checkout flow is failing.",
        "Priya is running the campaign for the Q3 launch.",
        "The race condition in the retry handler is fixed.",
        "Church Street office has the better meeting rooms.",
        # Ordinary work talk that mentions money.
        "We decided ₹299 for the Pro tier.",
        "Revenue is up 18% on last quarter.",
        "The renewal quote goes out on Thursday.",
        # Ordinary work talk that mentions a person.
        "Aditya will pull the churn numbers before the pricing call.",
        "The design review is on Thursday at 4.",
    ],
)
def test_ordinary_work_talk_is_not_refused(text):
    assert detect_sensitive(text) is None


def test_a_topic_word_alone_is_not_enough():
    """One signal is a coincidence; the rule needs two to agree."""
    assert detect_sensitive("Therapy for the codebase, honestly.") is None


def test_a_context_word_alone_is_not_enough():
    assert detect_sensitive("My laptop is playing up again.") is None


def test_a_bare_price_is_not_a_card_number():
    """The card rule matches 13 digits and up; prices never reach that."""
    assert detect_sensitive("It came to 1299 rupees.") is None
    assert detect_sensitive("Invoice 20260904 went out today.") is None


# --- behaviour --------------------------------------------------------------


def test_empty_input_is_not_sensitive():
    assert detect_sensitive("") is None
    assert detect_sensitive(None) is None


def test_the_category_reported_is_stable():
    """Two categories at once must always report the same one, or the log
    stops being reproducible across runs."""
    text = "My biopsy results are in, and the card number is 4111 1111 1111 1111."
    assert detect_sensitive(text) == detect_sensitive(text) == "financial_account"
