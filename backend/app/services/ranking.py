"""How memories are ordered, and how age is allowed to matter.

Two numbers, kept apart. Confidence is how well corroborated a claim is: it
comes from the evidence count, only grows, and is never touched here --
"you said this four times" stays true no matter how long ago it was.
Currency is how likely it is still worth volunteering, and it is computed
here, from the clock, and stored nowhere.

Age is a preference, not a filter. An old memory sinks; it does not become
unreachable. If nothing newer answers the question, the ten-month-old note is
still the best thing we have, and a system that had quietly dropped it would
be worse than one that offers it with a caveat. Hence the floor below: decay
approaches it and never reaches zero.

Everything here is pure and takes the clock as an argument, so decay
behaviour can be tested at any age without waiting for one.
"""

import math
from datetime import datetime, timedelta

from app.models.memory import HALF_LIFE_DAYS

# How far currency can fall. Well below anything recent, so fresh material
# wins comfortably, but never zero: nothing is ranked out of existence.
CURRENCY_FLOOR = 0.15

# Being used is worth a nudge, not an argument. Log-scaled and capped, so a
# frequently cited memory edges ahead without a popular one burying a more
# relevant answer.
USE_WEIGHT = 0.08
MAX_USE_BOOST = 1.4

DEFAULT_HALF_LIFE_DAYS = HALF_LIFE_DAYS["fact"]


def half_life(memory_type: str) -> timedelta:
    """How fast this kind of claim stops being worth volunteering."""
    days = HALF_LIFE_DAYS.get(memory_type, DEFAULT_HALF_LIFE_DAYS)
    return timedelta(days=days)


def currency(
    memory_type: str,
    last_reinforced_at: datetime | None,
    now: datetime,
) -> float:
    """How current this claim is, between the floor and 1.0.

    Halves once per half-life. A preference stated a year ago is still worth
    most of its weight; a decision from a year ago is worth very little, which
    is the whole reason the half-life depends on the type.
    """
    if last_reinforced_at is None:
        # Never reinforced means we do not know when it was last true, which
        # is not the same as knowing it is stale.
        return CURRENCY_FLOOR + (1.0 - CURRENCY_FLOOR) * 0.5

    age = now - last_reinforced_at
    if age <= timedelta(0):
        # A clock skew or a future-dated import should not score above fresh.
        return 1.0

    halvings = age / half_life(memory_type)
    return CURRENCY_FLOOR + (1.0 - CURRENCY_FLOOR) * (0.5**halvings)


def use_boost(use_count: int) -> float:
    """A mild lift for claims that have actually been useful."""
    return min(MAX_USE_BOOST, 1.0 + USE_WEIGHT * math.log1p(max(use_count, 0)))


def score(memory, now: datetime) -> float:
    """How strongly this memory should be preferred, all else equal.

    Confidence says how sure we are, currency says how likely it still holds,
    use says whether it has earned its place. Multiplied rather than added:
    a claim that is stale enough should sink however well corroborated it is,
    which addition would not allow.
    """
    return (
        float(memory.posterior_mean or 0.0)
        * currency(memory.type, memory.last_reinforced_at, now)
        * use_boost(memory.use_count or 0)
    )


def is_stale(memory, now: datetime, threshold: float = 0.4) -> bool:
    """Whether an answer drawing on this should say how old it is.

    Not a reason to withhold anything. It is the difference between "your
    price is ₹299" and "you said ₹299, though that was back in March" -- the
    second is the honest sentence when nothing more recent exists.
    """
    return currency(memory.type, memory.last_reinforced_at, now) < threshold


def is_expired(memory, now: datetime) -> bool:
    """Whether a commitment's own deadline has passed.

    The one hard cut here, and only because a past deadline volunteered as
    current is not merely stale, it is wrong. Nothing is deleted: an expired
    commitment stays answerable when asked about directly.
    """
    return memory.valid_until is not None and memory.valid_until <= now
