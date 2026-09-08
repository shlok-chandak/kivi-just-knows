"""Ordering, and how much age is allowed to matter.

The clock is an argument, so a year of decay is one line rather than a wait.
The theme running through these: newer is preferred, older is not discarded.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.ranking import (
    CURRENCY_FLOOR,
    currency,
    is_expired,
    is_stale,
    score,
    use_boost,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


@dataclass
class FakeMemory:
    type: str = "fact"
    posterior_mean: float = 0.67
    last_reinforced_at: datetime | None = NOW
    use_count: int = 0
    valid_until: datetime | None = None


def ago(**kwargs) -> datetime:
    return NOW - timedelta(**kwargs)


# --- currency ---------------------------------------------------------------


def test_something_said_just_now_is_fully_current():
    assert currency("fact", NOW, NOW) == 1.0


def test_currency_halves_over_a_half_life():
    """A fact's half-life is 90 days, so 90 days back should be mid-scale."""
    value = currency("fact", ago(days=90), NOW)
    midpoint = CURRENCY_FLOOR + (1.0 - CURRENCY_FLOOR) * 0.5
    assert abs(value - midpoint) < 0.01


def test_older_always_scores_lower_than_newer():
    ages = [currency("fact", ago(days=d), NOW) for d in (0, 30, 90, 365, 1000)]
    assert ages == sorted(ages, reverse=True)


def test_age_never_reaches_zero():
    """An old memory sinks; it does not become unreachable."""
    ancient = currency("decision", ago(days=4000), NOW)
    assert ancient >= CURRENCY_FLOOR
    assert ancient > 0


def test_a_preference_ages_far_more_slowly_than_a_decision():
    """People do not change how they like their email written every month."""
    year = ago(days=365)
    assert currency("preference", year, NOW) > currency("decision", year, NOW) * 2


def test_an_unknown_type_is_treated_like_a_fact():
    assert currency("something_new", ago(days=90), NOW) == currency(
        "fact", ago(days=90), NOW
    )


def test_a_never_reinforced_memory_is_not_assumed_stale():
    """Not knowing when it was last true is not the same as knowing it is old."""
    value = currency("fact", None, NOW)
    assert CURRENCY_FLOOR < value < 1.0


def test_a_future_timestamp_does_not_outrank_the_present():
    """Clock skew and future-dated imports must not score above fresh."""
    assert currency("fact", NOW + timedelta(days=5), NOW) == 1.0


# --- the combined score -----------------------------------------------------


def test_the_same_claim_ranks_higher_when_it_is_newer():
    """The question this whole module exists to answer."""
    fresh = FakeMemory(last_reinforced_at=NOW)
    old = FakeMemory(last_reinforced_at=ago(days=365))
    assert score(fresh, NOW) > score(old, NOW)


def test_a_fresh_single_mention_beats_a_stale_well_corroborated_one():
    """Repetition long ago should not outrank what was just said."""
    once_today = FakeMemory(posterior_mean=0.67, last_reinforced_at=NOW)
    often_last_year = FakeMemory(
        posterior_mean=0.90, last_reinforced_at=ago(days=400)
    )
    assert score(once_today, NOW) > score(often_last_year, NOW)


def test_corroboration_still_decides_between_equally_fresh_claims():
    weak = FakeMemory(posterior_mean=0.67)
    strong = FakeMemory(posterior_mean=0.90)
    assert score(strong, NOW) > score(weak, NOW)


def test_restating_an_old_claim_makes_it_current_again():
    old = FakeMemory(last_reinforced_at=ago(days=400))
    before = score(old, NOW)
    old.last_reinforced_at = NOW
    assert score(old, NOW) > before


def test_being_useful_nudges_without_deciding():
    """A popular memory should edge ahead, not bury a better answer."""
    popular_but_old = FakeMemory(last_reinforced_at=ago(days=365), use_count=50)
    quiet_but_fresh = FakeMemory(last_reinforced_at=NOW, use_count=0)
    assert score(quiet_but_fresh, NOW) > score(popular_but_old, NOW)

    used = FakeMemory(use_count=10)
    unused = FakeMemory(use_count=0)
    assert score(used, NOW) > score(unused, NOW)


def test_use_boost_is_capped():
    assert use_boost(10_000) == use_boost(1_000_000)


def test_a_negative_use_count_does_not_break_the_score():
    assert use_boost(-5) == 1.0


# --- staleness and expiry ---------------------------------------------------


def test_a_recent_memory_is_not_flagged_stale():
    assert not is_stale(FakeMemory(), NOW)


def test_a_long_unmentioned_decision_is_flagged_stale():
    """Flagged, not withheld: the answer says how old it is."""
    assert is_stale(FakeMemory(type="decision", last_reinforced_at=ago(days=200)), NOW)


def test_a_commitment_past_its_deadline_is_expired():
    assert is_expired(FakeMemory(valid_until=ago(days=1)), NOW)


def test_a_commitment_still_due_is_not_expired():
    assert not is_expired(FakeMemory(valid_until=NOW + timedelta(days=1)), NOW)


def test_a_memory_with_no_deadline_never_expires():
    assert not is_expired(FakeMemory(valid_until=None), NOW)
