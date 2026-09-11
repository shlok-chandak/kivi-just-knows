"""Turning "yesterday" into two timestamps.

Code, not a model call. Two reasons, and the second is the one that bites.

A model asked for a date will produce one that looks right and is off by a
day at a month boundary, or in the wrong timezone, and nothing downstream can
tell. This is arithmetic; arithmetic belongs in code.

And the reference point has to be injectable. The corpus is synthetic and
sits in the past, so "yesterday" during evaluation means yesterday relative
to the corpus clock. Resolving against the wall clock returns an empty range
and the system abstains -- correctly, on a question it should have answered.
Nothing about that failure looks like a clock problem.

The parser's job is to emit the expression the user used. This module turns
it into a range, or says it could not.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

# Windows around a stated time of day. "Around 5 PM" is not 17:00:00, and a
# range narrow enough to miss the thing being looked for is worse than none.
CLOCK_WINDOW = timedelta(minutes=90)

logger = logging.getLogger("kivi.timeref")

# Rough parts of the day, for "this morning" and the like.
DAY_PARTS = {
    "morning": (time(5), time(12)),
    "afternoon": (time(12), time(17)),
    "evening": (time(17), time(22)),
    "night": (time(22), time(5)),
}

WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
# Abbreviations, spelled out rather than matched as a three-letter prefix.
# A prefix reads "decided" as December and "margin" as March, which is a
# mistake this codebase has already made once, in claim comparison.
MONTHS |= {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH_WORDS = "|".join(sorted(MONTHS, key=len, reverse=True))

# "23 June", "June 23", "the 23rd of June". The day is optional; without
# one the expression names the whole month.
_MONTH_DAY = re.compile(
    rf"\b(?P<day1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m1>{_MONTH_WORDS})\b"
    rf"|\b(?P<m2>{_MONTH_WORDS})\s+(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_MONTH_ONLY = re.compile(rf"\b(?P<month>{_MONTH_WORDS})\b", re.IGNORECASE)

_CLOCK = re.compile(
    r"\b(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)\b"
    r"|\b(?P<h24>[01]?\d|2[0-3]):(?P<m24>\d{2})\b",
    re.IGNORECASE,
)

# Small numbers get spelled out in speech far more often than they get
# typed. "two weeks ago" is the natural phrasing; "2 weeks ago" is the
# transcribed one, and both have to land in the same place.
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "a": 1, "an": 1, "couple of": 2, "few": 3,
}
_COUNT_WORDS = "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))

# "last 3 days", "two weeks ago", "a month back". Trailing "ago" and
# "back" are how the same span is said looking backwards, and leaving
# them out meant the commonest phrasing of all resolved to nothing.
_LAST_N = re.compile(
    rf"\b(?:last|past|previous)\s+(?P<n>\d+|{_COUNT_WORDS})\s+"
    rf"(?P<unit>day|week|month)s?\b"
    rf"|\b(?P<n2>\d+|{_COUNT_WORDS})\s+(?P<unit2>day|week|month)s?"
    rf"\s+(?:ago|back)\b",
    re.I,
)

# Words that turn a point in time into a range with one end open. "Monday" is
# one day; "since Monday" is Monday until now. A closed set from grammar
# rather than a guess about vocabulary, so it can be listed rather than
# sampled.
_OPENS_FORWARD = ("since ", "from ", "after ", "starting ")
_CLOSES_BACKWARD = ("until ", "till ", "up to ", "before ")


@dataclass(frozen=True)
class Range:
    """A half-open interval. `start` is inclusive, `end` exclusive.

    Either end may be None, meaning unbounded that way. "Before Friday" has
    no beginning, and inventing one would be a filter nobody asked for.
    """

    start: datetime | None
    end: datetime | None
    expression: str


def _day(reference: datetime, on: date) -> tuple[datetime, datetime]:
    start = datetime.combine(on, time.min, tzinfo=reference.tzinfo or timezone.utc)
    return start, start + timedelta(days=1)


def _clock_window(reference: datetime, on: date, at: time) -> tuple[datetime, datetime]:
    centre = datetime.combine(on, at, tzinfo=reference.tzinfo or timezone.utc)
    return centre - CLOCK_WINDOW, centre + CLOCK_WINDOW


def _time_of_day(text: str) -> time | None:
    match = _CLOCK.search(text)
    if match is None:
        return None
    if match.group("h24") is not None:
        return time(int(match.group("h24")), int(match.group("m24")))

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = match.group("meridiem").lower()
    if meridiem == "pm" and hour != 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _count(raw: str) -> int:
    """A quantity written as digits or as a word."""
    return int(raw) if raw.isdigit() else _WORD_NUMBERS[raw.lower()]


def _most_recent(month: int, reference: datetime) -> int:
    """The year that makes this month the most recent one already past.

    Same rule as a bare weekday: "in June" asked in September means this
    June, and asked in March means last June. Looking forward would answer
    a question about the past with a range that has not happened yet.
    """
    return reference.year if month <= reference.month else reference.year - 1


def _month_range(
    month: int, reference: datetime
) -> tuple[datetime, datetime]:
    """The whole of a month, as a half-open interval."""
    tzinfo = reference.tzinfo or timezone.utc
    year = _most_recent(month, reference)
    start = datetime(year, month, 1, tzinfo=tzinfo)
    end = (
        datetime(year + 1, 1, 1, tzinfo=tzinfo)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=tzinfo)
    )
    return start, end


def _dated(text: str, reference: datetime) -> date | None:
    """A day named with a month: "23 June", "June 23", "the 23rd of June"."""
    match = _MONTH_DAY.search(text)
    if match is None:
        return None

    name = match.group("m1") or match.group("m2")
    raw_day = match.group("day1") or match.group("day2")
    month = MONTHS[name.lower()]
    day = int(raw_day)

    try:
        return date(_most_recent(month, reference), month, day)
    except ValueError:
        # The 31st of a thirty-day month. Refused rather than clamped: a
        # date nobody named is worse than admitting the expression is not
        # one this can resolve.
        logger.warning("discarding impossible date %s %s", raw_day, name)
        return None


def _named_day(text: str, reference: datetime) -> date | None:
    """The date a day-word refers to, or None if the text names none."""
    today = reference.date()
    if "yesterday" in text:
        return today - timedelta(days=1)
    if "today" in text or "this morning" in text or "this afternoon" in text:
        return today
    if "tomorrow" in text:
        return today + timedelta(days=1)

    for name, index in WEEKDAYS.items():
        if name not in text:
            continue
        # Bare "on Tuesday" means the most recent one. Looking forward would
        # answer a question about the past with an empty future range.
        behind = (today.weekday() - index) % 7 or 7
        if "next" in text:
            return today + timedelta(days=(index - today.weekday()) % 7 or 7)
        return today - timedelta(days=behind)

    return None


def _checked(span: Range | None) -> Range | None:
    """Refuse a range that cannot match anything.

    A range ending before it starts is a bug, and the shape it takes is a
    search that quietly returns nothing -- indistinguishable from "you never
    said that". Falling back to no filter searches too much, which is
    visible and recoverable.
    """
    if span is None:
        return None
    if span.start is not None and span.end is not None and span.end <= span.start:
        logger.warning("discarding impossible range for %r", span.expression)
        return None
    return span


def _anchor(text: str) -> str | None:
    """Whether a leading word opens or closes the range, and which way."""
    for word in _OPENS_FORWARD:
        if word in text:
            return "forward"
    for word in _CLOSES_BACKWARD:
        if word in text:
            return "backward"
    return None


def resolve(expression: str | None, *, now: datetime) -> Range | None:
    """The range an expression names, or None if it names none."""
    span = _resolve(expression, now=now)
    if span is None or expression is None:
        return _checked(span)

    direction = _anchor(expression.strip().lower())
    if direction is None:
        return _checked(span)

    # The inner resolver found the point; the anchor decides the shape.
    # "Since Monday" is Monday onwards, not Monday alone.
    if direction == "forward":
        return _checked(Range(span.start, now, expression))
    return _checked(Range(None, span.start, expression))


def _resolve(expression: str | None, *, now: datetime) -> Range | None:
    """The range an expression names, or None if it names none.

    None means "no time constraint", which is different from an empty range.
    A caller that treats them alike turns an unparsed expression into a
    search over nothing.
    """
    if not expression:
        return None

    text = expression.strip().lower()
    if not text or text in {"any", "anytime", "ever", "all time"}:
        return None

    tzinfo = now.tzinfo or timezone.utc

    match = _LAST_N.search(text)
    if match:
        count = _count(match.group("n") or match.group("n2"))
        unit = match.group("unit") or match.group("unit2")
        days = {"day": 1, "week": 7, "month": 30}[unit] * count
        return Range(now - timedelta(days=days), now, expression)

    for phrase, days in (
        ("last week", 7), ("past week", 7), ("this week", 7),
        ("last month", 30), ("past month", 30), ("this month", 30),
        ("recently", 7), ("lately", 14),
    ):
        if phrase in text:
            return Range(now - timedelta(days=days), now, expression)

    # A month with a day in it is a day; a month on its own is the month.
    # Checked before the bare-month case so "23rd of June" does not
    # resolve to the whole of June.
    on = _dated(text, now) or _named_day(text, now)
    at = _time_of_day(text)

    if on is None and at is None:
        month = _MONTH_ONLY.search(text)
        if month is not None:
            start, end = _month_range(MONTHS[month.group("month").lower()], now)
            return Range(start, end, expression)
        return None

    # A time with no day means today, which is what "around 5" means when
    # said at six.
    if on is None:
        on = now.date()

    if at is not None:
        start, end = _clock_window(now, on, at)
        return Range(start, end, expression)

    for part, (from_time, to_time) in DAY_PARTS.items():
        if part in text:
            start = datetime.combine(on, from_time, tzinfo=tzinfo)
            end = datetime.combine(on, to_time, tzinfo=tzinfo)
            if end <= start:  # "night" runs past midnight
                end += timedelta(days=1)
            return Range(start, end, expression)

    start, end = _day(now, on)
    return Range(start, end, expression)
