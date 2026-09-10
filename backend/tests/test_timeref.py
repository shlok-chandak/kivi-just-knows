"""Resolving time expressions.

Pure arithmetic against an injected clock. Every case here is one a model
would plausibly get wrong in a way nothing downstream could detect.
"""

from datetime import datetime, timedelta, timezone

from app.services.timeref import CLOCK_WINDOW, resolve

# A Wednesday, deliberately: weekday arithmetic is where off-by-one lives.
NOW = datetime(2026, 9, 9, 18, 30, tzinfo=timezone.utc)


def test_no_expression_means_no_constraint():
    """None is not an empty range: one searches everything, one searches nothing."""
    assert resolve(None, now=NOW) is None
    assert resolve("", now=NOW) is None
    assert resolve("anytime", now=NOW) is None


def test_an_unparseable_expression_does_not_invent_a_range():
    assert resolve("when the vendor thing happened", now=NOW) is None


def test_yesterday_is_a_whole_day():
    span = resolve("yesterday", now=NOW)
    assert span.start == datetime(2026, 9, 8, tzinfo=timezone.utc)
    assert span.end == datetime(2026, 9, 9, tzinfo=timezone.utc)


def test_yesterday_is_relative_to_the_injected_clock():
    """The reason the clock is injectable at all.

    The corpus sits in the past. Resolved against the wall clock this returns
    an empty range and the system abstains on a question it could answer.
    """
    corpus_now = datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)
    span = resolve("yesterday", now=corpus_now)
    assert span.start.date() == corpus_now.date() - timedelta(days=1)


def test_around_five_pm_yesterday_is_a_window_not_an_instant():
    """The brief's own example. An exact second would find nothing."""
    span = resolve("around 5 PM yesterday", now=NOW)
    centre = datetime(2026, 9, 8, 17, 0, tzinfo=timezone.utc)
    assert span.start == centre - CLOCK_WINDOW
    assert span.end == centre + CLOCK_WINDOW


def test_midday_and_midnight_convert_correctly():
    """12 AM and 12 PM are where every hand-rolled parser goes wrong."""
    assert resolve("12 pm today", now=NOW).start.hour == 10  # noon less 90 min
    assert resolve("12 am today", now=NOW).start.date() == datetime(
        2026, 9, 8, tzinfo=timezone.utc
    ).date()


def test_twenty_four_hour_times_are_understood():
    span = resolve("at 17:30 yesterday", now=NOW)
    assert span.start == datetime(2026, 9, 8, 16, 0, tzinfo=timezone.utc)


def test_a_bare_weekday_means_the_most_recent_one():
    """Looking forward would answer a question about the past with a future."""
    span = resolve("on monday", now=NOW)
    assert span.start == datetime(2026, 9, 7, tzinfo=timezone.utc)


def test_the_same_weekday_as_today_means_a_week_ago():
    """Not today. "On Wednesday" said on a Wednesday means the last one."""
    span = resolve("on wednesday", now=NOW)
    assert span.start == datetime(2026, 9, 2, tzinfo=timezone.utc)


def test_next_weekday_looks_forward():
    span = resolve("next monday", now=NOW)
    assert span.start == datetime(2026, 9, 14, tzinfo=timezone.utc)


def test_this_morning_is_a_part_of_today():
    span = resolve("this morning", now=NOW)
    assert span.start == datetime(2026, 9, 9, 5, 0, tzinfo=timezone.utc)
    assert span.end == datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


def test_last_week_ends_now_rather_than_at_midnight():
    span = resolve("last week", now=NOW)
    assert span.end == NOW
    assert span.start == NOW - timedelta(days=7)


def test_last_n_days_is_counted():
    assert resolve("last 3 days", now=NOW).start == NOW - timedelta(days=3)
    assert resolve("past 2 weeks", now=NOW).start == NOW - timedelta(days=14)


def test_a_time_with_no_day_means_today():
    span = resolve("around 9am", now=NOW)
    assert span.start.date() == NOW.date()


def test_the_expression_is_carried_through():
    """The trace has to say what was resolved, not just the numbers."""
    assert resolve("around 5 PM yesterday", now=NOW).expression == "around 5 PM yesterday"


# --- words that open or close a range ----------------------------------------


def test_since_a_weekday_runs_up_to_now():
    """The bug this section exists for.

    "Since Monday" resolved to Monday alone, so a question about the last
    three days searched one and found nothing -- which reads exactly like
    having nothing to say.
    """
    span = resolve("since monday", now=NOW)
    assert span.start == datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert span.end == NOW


def test_every_opening_word_runs_forward():
    for phrase in ("since tuesday", "from tuesday", "after tuesday", "starting tuesday"):
        span = resolve(phrase, now=NOW)
        assert span.start == datetime(2026, 9, 8, tzinfo=timezone.utc), phrase
        assert span.end == NOW, phrase


def test_closing_words_leave_the_start_open():
    """"Before Friday" has no beginning; inventing one is a filter nobody asked for."""
    for phrase in ("until friday", "before friday", "till friday", "up to friday"):
        span = resolve(phrase, now=NOW)
        assert span.start is None, phrase
        assert span.end == datetime(2026, 9, 4, tzinfo=timezone.utc), phrase


def test_an_anchor_works_on_any_kind_of_point():
    assert resolve("since yesterday", now=NOW).end == NOW
    assert resolve("since last week", now=NOW).end == NOW


def test_a_bare_weekday_is_still_a_single_day():
    """The anchor changes the shape; without one nothing changes."""
    span = resolve("monday", now=NOW)
    assert span.start == datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert span.end == datetime(2026, 9, 8, tzinfo=timezone.utc)


def test_an_impossible_range_is_refused_rather_than_returned():
    """The general guard, not a case.

    A range that ends before it starts matches nothing, and searching nothing
    is indistinguishable from having nothing to say. No filter searches too
    much instead, which is visible.
    """
    from app.services.timeref import Range, _checked

    backwards = Range(NOW, NOW - timedelta(days=1), "nonsense")
    assert _checked(backwards) is None
    assert _checked(Range(NOW, NOW, "empty")) is None
    assert _checked(Range(None, NOW, "open start")) is not None
    assert _checked(Range(NOW, None, "open end")) is not None
