"""Tests for the per-trading-day PnL tracker.

The trading day is anchored to **17:00 America/New_York**, not to UTC midnight and not
to the host's zone. That choice is what makes "daily" mean something for a market that
closes at 17:00 New York, and it is enforced with the pinned ``tzdata`` zone rather than
the host's local time.

The DST cases are the ones that would break quietly: across the spring-forward and
fall-back weekends the *wall clock* still rolls at 17:00, so the trading day contains
23 or 25 hours of real time. A UTC-offset implementation gets those two days wrong and
is right the other 363 days of the year, which is exactly the kind of bug that survives
testing.
"""

import datetime as dt
from decimal import Decimal

import pytest

from engine.risk.drawdown import (
    DAILY_ROLLOVER,
    NEW_YORK,
    DailyPnLTracker,
    trading_day_bounds,
    trading_day_for,
)

UTC = dt.UTC

#: 2026-01-15 is a Thursday. New York is on EST (UTC-5) all month, so 17:00 local is
#: 22:00 UTC.
THURSDAY = dt.datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
THURSDAY_BEFORE_ROLLOVER = dt.datetime(2026, 1, 15, 21, 59, 59, 999999, tzinfo=UTC)
THURSDAY_AT_ROLLOVER = dt.datetime(2026, 1, 15, 22, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# the anchor
# --------------------------------------------------------------------------- #
def test_the_rollover_is_17h00_new_york() -> None:
    assert dt.time(17, 0) == DAILY_ROLLOVER
    assert str(NEW_YORK) == "America/New_York"


def test_a_ts_before_the_rollover_belongs_to_the_previous_day() -> None:
    assert trading_day_for(THURSDAY) == dt.date(2026, 1, 14)


def test_the_rollover_instants_starts_the_new_day() -> None:
    """Half-open: the instant the clock strikes 17:00 is the first of the new day."""
    assert trading_day_for(THURSDAY_AT_ROLLOVER) == dt.date(2026, 1, 15)


def test_one_microsecond_before_the_rollover_is_still_the_old_day() -> None:
    assert trading_day_for(THURSDAY_BEFORE_ROLLOVER) == dt.date(2026, 1, 14)


def test_the_day_bounds_are_the_rollover_to_the_rollover() -> None:
    assert trading_day_bounds(dt.date(2026, 1, 15)) == (
        dt.datetime(2026, 1, 15, 22, 0, tzinfo=UTC),
        dt.datetime(2026, 1, 16, 22, 0, tzinfo=UTC),
    )


def test_a_winter_trading_day_is_24_hours() -> None:
    start, end = trading_day_bounds(dt.date(2026, 1, 15))

    assert end - start == dt.timedelta(hours=24)


# --------------------------------------------------------------------------- #
# DST
# --------------------------------------------------------------------------- #
def test_spring_forward_the_rollover_moves_with_the_wall_clock() -> None:
    # 2026-03-08 is the spring-forward day: 17:00 local is EDT (UTC-4) that evening.
    assert trading_day_for(dt.datetime(2026, 3, 8, 20, 59, 59, tzinfo=UTC)) == dt.date(
        2026, 3, 7
    )
    assert trading_day_for(dt.datetime(2026, 3, 8, 21, 0, 0, tzinfo=UTC)) == dt.date(
        2026, 3, 8
    )


def test_the_day_that_contains_the_spring_transition_is_23_hours() -> None:
    start, end = trading_day_bounds(dt.date(2026, 3, 7))

    assert start == dt.datetime(2026, 3, 7, 22, 0, tzinfo=UTC)  # EST
    assert end == dt.datetime(2026, 3, 8, 21, 0, tzinfo=UTC)  # EDT
    assert end - start == dt.timedelta(hours=23)


def test_the_first_day_after_the_spring_transition_is_24_hours() -> None:
    start, end = trading_day_bounds(dt.date(2026, 3, 8))

    assert end - start == dt.timedelta(hours=24)


def test_fall_back_the_rollover_moves_with_the_wall_clock() -> None:
    # 2026-11-01 is the fall-back day: 17:00 local is EST (UTC-5) that evening.
    assert trading_day_for(dt.datetime(2026, 11, 1, 21, 59, 59, tzinfo=UTC)) == dt.date(
        2026, 10, 31
    )
    assert trading_day_for(dt.datetime(2026, 11, 1, 22, 0, 0, tzinfo=UTC)) == dt.date(
        2026, 11, 1
    )


def test_the_day_that_contains_the_fall_transition_is_25_hours() -> None:
    start, end = trading_day_bounds(dt.date(2026, 10, 31))

    assert start == dt.datetime(2026, 10, 31, 21, 0, tzinfo=UTC)  # EDT
    assert end == dt.datetime(2026, 11, 1, 22, 0, tzinfo=UTC)  # EST
    assert end - start == dt.timedelta(hours=25)


def test_the_anchor_is_wall_clock_not_a_fixed_utc_hour() -> None:
    """Both DST transitions land on 17:00 local, so no single UTC hour is correct."""
    assert trading_day_for(dt.datetime(2026, 7, 15, 20, 59, 59, tzinfo=UTC)) == dt.date(
        2026, 7, 14
    )
    assert trading_day_for(dt.datetime(2026, 7, 15, 21, 0, 0, tzinfo=UTC)) == dt.date(
        2026, 7, 15
    )


# --------------------------------------------------------------------------- #
# input handling
# --------------------------------------------------------------------------- #
def test_a_naive_timestamp_is_refused() -> None:
    with pytest.raises(ValueError):
        trading_day_for(dt.datetime(2026, 1, 15, 15, 0))  # type: ignore[arg-type]


def test_another_zone_is_converted_not_reinterpreted() -> None:
    """The same instant in Tokyo must map to the same New York trading day."""
    tokyo = dt.timezone(dt.timedelta(hours=9))
    instant = dt.datetime(2026, 1, 16, 7, 0, tzinfo=tokyo)  # == 2026-01-15 22:00 UTC

    assert trading_day_for(instant) == dt.date(2026, 1, 15)


def test_a_custom_rollover_and_zone_are_honoured() -> None:
    custom = dt.timezone(dt.timedelta(hours=5, minutes=30))

    # 10:00 UTC is 15:30 local, before an 18:00 rollover, so it is still yesterday.
    assert trading_day_for(
        dt.datetime(2026, 1, 15, 10, 0, tzinfo=UTC), tz=custom, rollover=dt.time(18, 0)
    ) == dt.date(2026, 1, 14)
    # 14:00 UTC is 19:30 local, so the same calendar day is now current.
    assert trading_day_for(
        dt.datetime(2026, 1, 15, 14, 0, tzinfo=UTC), tz=custom, rollover=dt.time(18, 0)
    ) == dt.date(2026, 1, 15)


# --------------------------------------------------------------------------- #
# the tracker
# --------------------------------------------------------------------------- #
def test_a_tracker_starts_empty() -> None:
    tracker = DailyPnLTracker()

    assert tracker.days == ()
    current = tracker.current(THURSDAY)

    assert current.day == dt.date(2026, 1, 14)
    assert current.realized == Decimal(0)
    assert current.unrealized == Decimal(0)
    assert current.total == Decimal(0)
    assert current.total_r == Decimal(0)


def test_realized_accumulates_within_a_day() -> None:
    tracker = DailyPnLTracker()

    tracker.record_realized(Decimal("-100"), THURSDAY)
    tracker.record_realized(Decimal("40"), THURSDAY)

    assert tracker.current(THURSDAY).realized == Decimal("-60")


def test_realized_is_recorded_against_the_day_that_owns_the_instants() -> None:
    tracker = DailyPnLTracker()

    tracker.record_realized(Decimal("-100"), THURSDAY)
    tracker.record_realized(Decimal("500"), THURSDAY_AT_ROLLOVER)

    assert tracker.totals(dt.date(2026, 1, 14)).realized == Decimal("-100")
    assert tracker.totals(dt.date(2026, 1, 15)).realized == Decimal("500")
    assert tracker.days == (dt.date(2026, 1, 14), dt.date(2026, 1, 15))


def test_unrealized_is_a_mark_not_an_accumulation() -> None:
    """Unrealized PnL is the current mark of what is open, so a new mark replaces it."""
    tracker = DailyPnLTracker()

    tracker.record_unrealized(Decimal("120"), THURSDAY)
    tracker.record_unrealized(Decimal("35"), THURSDAY)

    assert tracker.current(THURSDAY).unrealized == Decimal("35")
    assert tracker.current(THURSDAY).total == Decimal("35")


def test_total_is_realized_plus_unrealized() -> None:
    tracker = DailyPnLTracker()

    tracker.record_realized(Decimal("300"), THURSDAY)
    tracker.record_unrealized(Decimal("-80"), THURSDAY)

    assert tracker.current(THURSDAY).total == Decimal("220")


def test_r_multiples_are_tracked_alongside_the_currency() -> None:
    tracker = DailyPnLTracker()

    tracker.record_realized(Decimal("-100"), THURSDAY, r_multiple=Decimal("-1"))
    tracker.record_realized(Decimal("-150"), THURSDAY, r_multiple=Decimal("-1.5"))

    current = tracker.current(THURSDAY)

    assert current.realized == Decimal("-250")
    assert current.realized_r == Decimal("-2.5")
    assert current.total_r == Decimal("-2.5")


def test_an_unrealized_mark_carries_its_own_r_multiple() -> None:
    tracker = DailyPnLTracker()

    tracker.record_unrealized(Decimal("300"), THURSDAY, r_multiple=Decimal("3"))

    current = tracker.current(THURSDAY)

    assert current.unrealized == Decimal("300")
    assert current.unrealized_r == Decimal("3")
    assert current.total_r == Decimal("3")


def test_a_rollover_flushes_the_previous_day() -> None:
    tracker = DailyPnLTracker()

    tracker.record_realized(Decimal("-250"), THURSDAY)
    day_after = dt.datetime(2026, 1, 16, 22, 0, tzinfo=UTC)

    assert tracker.current(day_after).day == dt.date(2026, 1, 16)
    assert tracker.current(day_after).realized == Decimal(0)
    assert tracker.totals(dt.date(2026, 1, 14)).realized == Decimal("-250")


def test_days_are_reported_in_order() -> None:
    tracker = DailyPnLTracker()

    tracker.record_realized(Decimal("1"), THURSDAY)
    tracker.record_realized(Decimal("2"), THURSDAY_AT_ROLLOVER)
    tracker.record_realized(Decimal("3"), THURSDAY_BEFORE_ROLLOVER)

    assert tracker.days == (dt.date(2026, 1, 14), dt.date(2026, 1, 15))


def test_querying_an_unknown_day_returns_none() -> None:
    tracker = DailyPnLTracker()

    tracker.record_realized(Decimal("-250"), THURSDAY)

    assert tracker.totals(dt.date(2026, 1, 15)) is None


def test_the_tracker_refuses_a_naive_instants() -> None:
    tracker = DailyPnLTracker()

    with pytest.raises(ValueError):
        tracker.record_realized(Decimal("1"), dt.datetime(2026, 1, 15, 15, 0))  # type: ignore[arg-type]


def test_the_tracker_refuses_a_float_amount() -> None:
    tracker = DailyPnLTracker()

    with pytest.raises(ValueError):
        tracker.record_realized(-100.0, THURSDAY)  # type: ignore[arg-type]


def test_the_rollover_stays_correct_across_a_dst_boundary() -> None:
    """A loss booked just before the 17:00 rollover on a fall-back day is yesterday's."""
    tracker = DailyPnLTracker()

    tracker.record_realized(
        Decimal("-250"), dt.datetime(2026, 11, 1, 21, 59, 59, tzinfo=UTC)
    )

    assert tracker.current(dt.datetime(2026, 11, 1, 22, 0, tzinfo=UTC)).day == dt.date(
        2026, 11, 1
    )
    assert tracker.totals(dt.date(2026, 10, 31)).realized == Decimal("-250")
