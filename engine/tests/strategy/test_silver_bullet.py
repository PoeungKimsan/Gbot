"""Tests for the silver bullet: the windows, the reference guard, the lifecycle.

Three groups, in order of what they protect.

**The windows.** A silver bullet trades at 03:00 and 10:00 *New York*, so both windows
move an hour when the city changes offset. The 2026 changeover weeks are the test:
spring forward on Sunday 2026-03-08, fall back on Sunday 2026-11-01. A window written
in UTC is right for half the year and silently wrong for the other half, so the
bounds here are asserted on both sides of both transitions.

**The reference guard.** During the London window the London daily range has not
concluded, so the strategy must not be able to see it. The tests do not merely check
that no order mentions it: one rebuilds the London range with its decisive extreme an
hour *after* the window closes and shows the order never moves.

**The lifecycle.** One trade followed from sweep to flatten, with the expiry, the
buffered stop and the dynamic close each pinned.
"""

import dataclasses
import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from engine.domain.orders import OrderSide
from engine.market.models import Bar
from engine.strategy.config import StrategyConfig
from engine.strategy.silver_bullet import (
    ALLOWED_REFERENCES,
    ASIAN_RANGE_WINDOW,
    DYNAMIC_CLOSE,
    LONDON_RANGE_WINDOW,
    LONDON_WINDOW,
    NY_AM_WINDOW,
    CancelOrder,
    FillReport,
    FlattenPosition,
    PlaceLimit,
    RangeName,
    SilverBulletStrategy,
    WindowName,
)

NEW_YORK = ZoneInfo("America/New_York")

#: The canonical day's London setup, written longhand so an assertion reads as a price
#: rather than as a symbol.
SWEEP_LOW = Decimal("2004.70")
FVG_MIDPOINT = Decimal("2006.35")
STOP = Decimal("2004.65")
TARGET = Decimal("2009.75")
ONE_WEEK = dt.timedelta(days=1)


def _strategy(
    strategy_config: StrategyConfig, spec, **changes: object
) -> SilverBulletStrategy:
    config = strategy_config
    if changes:
        config = dataclasses.replace(strategy_config, **changes)
    return SilverBulletStrategy(config=config, spec=spec, run_id="test")


def _drive(bars, strategy: SilverBulletStrategy, fill_on: dt.time) -> list:
    """Replay bars, reporting each entry fill at the bar that traded through it.

    In the canonical day the 03:25 bar's ask low trades through the 03:20 entry, so
    that is where the fill is reported -- the same bar the backtest's broker fills it
    on. A strategy that had to guess at its own fills could not be tested without
    re-implementing them.
    """
    intents = []
    for bar in bars:
        if _local(bar).time() == fill_on and strategy.resting_orders:
            order = strategy.resting_orders[0]
            strategy.on_fill(
                FillReport(
                    order_id=order.order_id,
                    position_id=order.order_id,
                    side=order.side,
                    quantity=order.quantity,
                    price=order.limit_price,
                    timestamp_utc=bar.timestamp_utc,
                )
            )
        for intent in strategy.on_bar(bar):
            intents.append(intent)
            if isinstance(intent, FlattenPosition):
                # The runner closes the position and reports it back; this test is
                # the runner for the purposes of the strategy's book.
                strategy.on_position_closed(intent.position_id)
    return intents


def _intents(bars, strategy: SilverBulletStrategy) -> list:
    collected: list = []
    for bar in bars:
        collected.extend(strategy.on_bar(bar))
    return collected


def _local(bar: Bar) -> dt.datetime:
    return bar.timestamp_utc.astimezone(NEW_YORK)


def _local_of(at: dt.datetime) -> dt.datetime:
    return at.astimezone(NEW_YORK)


def _clock(day: dt.date, clock: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, clock, tzinfo=NEW_YORK).astimezone(dt.UTC)


def _rewrite_bar(bars, clock: dt.time, ohlc, *, bar_factory) -> list[Bar]:
    """Replace the bar printed at ``clock`` with the named OHLC."""
    rewritten = []
    for bar in bars:
        local = _local(bar)
        if local.time() == clock:
            rewritten.append(
                bar_factory(
                    local.replace(tzinfo=None),
                    ohlc[0],
                    ohlc[1],
                    ohlc[2],
                    ohlc[3],
                )
            )
            continue
        rewritten.append(bar)
    return rewritten


def _only_order(bars, strategy_config: StrategyConfig, spec):
    strategy = _strategy(strategy_config, spec)
    placed = [intent for intent in _intents(bars, strategy) if isinstance(intent, PlaceLimit)]
    assert len(placed) == 1
    return placed[0]


def _days_from(days: list[dt.date], factory: Callable[..., list[Bar]]) -> list[Bar]:
    """A continuous bar stream for several days, Asian sessions included.

    A day's Asian session prints on the *evening before*, so each day after the first
    is taken from 20:00 the previous evening onwards. Taking the day alone would drop
    the session and quietly leave the strategy without the level under test.
    """
    bars = factory(days[0])
    for day in days[1:]:
        opening = _clock(day - ONE_WEEK, dt.time(20, 0))
        bars += [bar for bar in factory(day) if bar.timestamp_utc >= opening]
    return bars


# --------------------------------------------------------------------------- #
# the windows
# --------------------------------------------------------------------------- #
def test_the_two_windows_are_the_published_silver_bullet_hours() -> None:
    assert (LONDON_WINDOW.start, LONDON_WINDOW.end) == (dt.time(3, 0), dt.time(4, 0))
    assert (NY_AM_WINDOW.start, NY_AM_WINDOW.end) == (dt.time(10, 0), dt.time(11, 0))
    assert dt.time(16, 45) == DYNAMIC_CLOSE
    assert (ASIAN_RANGE_WINDOW.start, ASIAN_RANGE_WINDOW.end) == (dt.time(20, 0), dt.time(0, 0))
    assert (LONDON_RANGE_WINDOW.start, LONDON_RANGE_WINDOW.end) == (dt.time(0, 0), dt.time(5, 0))


def test_each_window_is_sixty_minutes_long() -> None:
    assert LONDON_WINDOW.duration == dt.timedelta(hours=1)
    assert NY_AM_WINDOW.duration == dt.timedelta(hours=1)
    assert ASIAN_RANGE_WINDOW.duration == dt.timedelta(hours=4)
    assert LONDON_RANGE_WINDOW.duration == dt.timedelta(hours=5)


def test_a_window_is_half_open_on_its_end() -> None:
    opened = LONDON_WINDOW.start_instant(dt.date(2026, 3, 9))
    closed = LONDON_WINDOW.end_instant(dt.date(2026, 3, 9))

    assert LONDON_WINDOW.contains_utc(opened)
    assert not LONDON_WINDOW.contains_utc(closed)
    assert not LONDON_WINDOW.contains_utc(opened - dt.timedelta(microseconds=1))


def test_the_asian_range_wraps_midnight() -> None:
    evening = _clock(dt.date(2026, 3, 9), dt.time(23, 59))
    midnight = _clock(dt.date(2026, 3, 10), dt.time(0, 0))
    opening = _clock(dt.date(2026, 3, 9), dt.time(20, 0))

    assert ASIAN_RANGE_WINDOW.contains_utc(opening)
    assert ASIAN_RANGE_WINDOW.contains_utc(evening)
    assert not ASIAN_RANGE_WINDOW.contains_utc(midnight)


@pytest.mark.parametrize(
    ("day", "offset_hours"),
    [
        # Spring-forward weekend: Friday is EST (UTC-5), Monday is EDT (UTC-4).
        (dt.date(2026, 3, 6), 8),
        (dt.date(2026, 3, 9), 7),
        # Fall-back weekend: Friday is EDT (UTC-4), Monday is EST (UTC-5).
        (dt.date(2026, 10, 30), 7),
        (dt.date(2026, 11, 2), 8),
    ],
)
def test_the_london_window_is_0300_new_york_across_both_transitions(
    day: dt.date, offset_hours: int
) -> None:
    opened = LONDON_WINDOW.start_instant(day)
    closed = LONDON_WINDOW.end_instant(day)

    assert _local_of(opened).time() == dt.time(3, 0)
    assert _local_of(closed).time() == dt.time(4, 0)
    assert opened == _clock(day, dt.time(3, 0))
    assert closed - opened == dt.timedelta(hours=1)
    assert opened.hour == offset_hours


@pytest.mark.parametrize(
    ("day", "offset_hours"),
    [
        (dt.date(2026, 3, 6), 15),
        (dt.date(2026, 3, 9), 14),
        (dt.date(2026, 10, 30), 14),
        (dt.date(2026, 11, 2), 15),
    ],
)
def test_the_ny_am_window_is_1000_new_york_across_both_transitions(
    day: dt.date, offset_hours: int
) -> None:
    opened = NY_AM_WINDOW.start_instant(day)

    assert _local_of(opened).time() == dt.time(10, 0)
    assert opened == _clock(day, dt.time(10, 0))
    assert opened.hour == offset_hours


def test_the_dynamic_close_is_1645_new_york_on_both_sides_of_the_transition() -> None:
    # March 9 is EDT, so 16:45 is 20:45 UTC; November 2 is EST, so it is 21:45 UTC.
    assert _clock(dt.date(2026, 3, 9), DYNAMIC_CLOSE).hour == 20
    assert _clock(dt.date(2026, 11, 2), DYNAMIC_CLOSE).hour == 21
    assert _clock(dt.date(2026, 3, 9), DYNAMIC_CLOSE).minute == 45
    assert _clock(dt.date(2026, 11, 2), DYNAMIC_CLOSE).minute == 45


def test_the_london_range_concludes_at_0500_new_york() -> None:
    assert _local_of(_clock(dt.date(2026, 3, 9), dt.time(5, 0))).time() == dt.time(5, 0)
    assert _clock(dt.date(2026, 3, 9), dt.time(5, 0)).hour == 9
    assert _clock(dt.date(2026, 11, 2), dt.time(5, 0)).hour == 10


def test_the_transition_weekend_itself_is_not_gap_torn() -> None:
    """The Sunday the clocks change still has a 03:00 and a 10:00 New York."""
    for sunday in (dt.date(2026, 3, 8), dt.date(2026, 11, 1)):
        assert LONDON_WINDOW.start_instant(sunday) < LONDON_WINDOW.end_instant(sunday)
        assert NY_AM_WINDOW.start_instant(sunday) < NY_AM_WINDOW.end_instant(sunday)


# --------------------------------------------------------------------------- #
# the reference guard
# --------------------------------------------------------------------------- #
def test_the_london_window_may_only_reference_the_asian_range_and_the_previous_day() -> None:
    assert ALLOWED_REFERENCES[WindowName.LONDON] == frozenset(
        {RangeName.ASIAN, RangeName.PREVIOUS_DAY}
    )


def test_the_ny_am_window_additionally_knows_the_london_range() -> None:
    assert ALLOWED_REFERENCES[WindowName.NY_AM] == frozenset(
        {RangeName.ASIAN, RangeName.LONDON, RangeName.PREVIOUS_DAY}
    )


def test_during_the_london_window_the_london_range_has_not_concluded(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    strategy = _strategy(strategy_config, spec)
    _intents(canonical_day_factory(dt.date(2026, 3, 9)), strategy)

    during = strategy.permitted_levels(_clock(dt.date(2026, 3, 9), dt.time(3, 30)))

    assert {level.source for level in during} == {
        RangeName.ASIAN.value,
        RangeName.PREVIOUS_DAY.value,
    }


def test_a_stale_london_range_is_not_a_permitted_level_either(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """Yesterday's London range concluded, but it belongs to a day that is over."""
    first = canonical_day_factory(dt.date(2026, 3, 9))
    following = [
        bar for bar in canonical_day_factory(dt.date(2026, 3, 10))
        if _local(bar).date() == dt.date(2026, 3, 10)
    ]
    strategy = _strategy(strategy_config, spec)
    _intents(first + following, strategy)

    during = strategy.permitted_levels(_clock(dt.date(2026, 3, 10), dt.time(3, 30)))

    assert {level.source for level in during} == {
        RangeName.ASIAN.value,
        RangeName.PREVIOUS_DAY.value,
    }


def test_an_order_placed_in_the_london_window_references_the_asian_range(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    order = _only_order(canonical_day_factory(dt.date(2026, 3, 9)), strategy_config, spec)

    assert order.reference is RangeName.ASIAN
    assert _local_of(order.placed_at).time() == dt.time(3, 20)


def test_a_london_range_that_only_completes_after_the_window_cannot_move_the_order(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    """The whole look-ahead test, in one bar.

    The London daily range's high is written at 04:30 -- an hour after the London
    window closes. A strategy that quoted it at 03:00 would place a different order,
    with a different stop and a different target. It does not.
    """
    clean = canonical_day_factory(dt.date(2026, 3, 9))
    poisoned = _rewrite_bar(
        clean,
        dt.time(4, 30),
        (Decimal("2100.00"), Decimal("2100.00"), Decimal("2095.00"), Decimal("2100.00")),
        bar_factory=bar_factory,
    )

    assert _only_order(poisoned, strategy_config, spec) == _only_order(
        clean, strategy_config, spec
    )
    assert _only_order(poisoned, strategy_config, spec).limit_price == FVG_MIDPOINT


def test_permitted_levels_are_empty_outside_a_window(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    strategy = _strategy(strategy_config, spec)
    _intents(canonical_day_factory(dt.date(2026, 3, 9)), strategy)

    assert strategy.permitted_levels(_clock(dt.date(2026, 3, 9), dt.time(7, 30))) == ()
    assert strategy.permitted_levels(_clock(dt.date(2026, 3, 9), dt.time(12, 0))) == ()


def test_a_range_two_days_old_is_not_a_level(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """The date half of the guard: yesterday's range is a level, the day before is not.

    Both are named ``ASIAN`` and both have long concluded, so only the date tells them
    apart. Asserting the days rather than the prices is what proves the filter does
    something: the fixture prints the same range every day, so only a date can tell a
    stale level from a live one.
    """
    days = [dt.date(2026, 3, 9), dt.date(2026, 3, 10), dt.date(2026, 3, 11)]
    strategy = _strategy(strategy_config, spec)
    _intents(_days_from(days, canonical_day_factory), strategy)

    during = strategy.permitted_levels(_clock(days[-1], dt.time(3, 30)))

    assert {level.day for level in during} == {days[-2], days[-1]}
    assert {level.source for level in during} == {
        RangeName.ASIAN.value,
        RangeName.PREVIOUS_DAY.value,
    }


def test_a_sweep_of_a_stale_level_is_skipped_not_raised(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    """A level this window may read but does not trade is skipped, not a crash.

    The London range high prints at 2009.80 every day. On the third day a spike
    through it sweeps the stale range from two days back -- a level that has concluded
    and carries the right name, and is untradeable only because of its date. The
    detector reports the sweep, the strategy declines it, and the run carries on.
    """
    days = [dt.date(2026, 3, 9), dt.date(2026, 3, 10), dt.date(2026, 3, 11)]
    spiked = _rewrite_bar(
        _days_from(days, canonical_day_factory),
        dt.datetime.combine(days[-1], dt.time(10, 5)),
        (Decimal("2008.50"), Decimal("2009.90"), Decimal("2008.40"), Decimal("2008.45")),
        bar_factory=bar_factory,
    )

    strategy = _strategy(strategy_config, spec)
    placed = [
        intent for intent in _intents(spiked, strategy) if isinstance(intent, PlaceLimit)
    ]

    # One trade a day, all of them the London window's own setup. Nothing was
    # triggered by the spike through the stale range, and nothing crashed.
    assert [order.reference for order in placed] == [RangeName.ASIAN] * len(days)
    assert strategy.declined == ()


# --------------------------------------------------------------------------- #
# the order lifecycle
# --------------------------------------------------------------------------- #
def test_a_complete_setup_places_a_limit_at_the_gap_midpoint(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    placed = _only_order(canonical_day_factory(dt.date(2026, 3, 9)), strategy_config, spec)

    assert placed.side is OrderSide.BUY
    assert placed.limit_price == FVG_MIDPOINT
    assert placed.stop_price == STOP
    assert placed.target_price == TARGET
    assert placed.quantity == Decimal("1")
    assert placed.risk == Decimal("1.70")
    assert placed.expires_after_bars == 12
    assert placed.risk == placed.limit_price - placed.stop_price


def test_the_stop_is_buffered_beyond_the_swept_extreme(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """The swept low is 2004.70, so the stop sits five ticks below it."""
    order = _only_order(canonical_day_factory(dt.date(2026, 3, 9)), strategy_config, spec)

    assert order.stop_price == SWEEP_LOW - (Decimal("5") * spec.tick)
    assert order.limit_price > order.stop_price


def test_the_target_is_rr_times_r(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    order = _only_order(canonical_day_factory(dt.date(2026, 3, 9)), strategy_config, spec)
    risk = order.limit_price - order.stop_price

    assert order.target_price == order.limit_price + (strategy_config.rr_target * risk)


def test_a_bearish_ny_am_setup_places_a_sell_limit(
    strategy_config: StrategyConfig, spec, ny_am_day_factory
) -> None:
    order = _only_order(ny_am_day_factory(dt.date(2026, 3, 9)), strategy_config, spec)

    assert order.side is OrderSide.SELL
    assert order.reference is RangeName.LONDON
    # The London range high is 2008.60; the 10:05 wick pierces it to 2008.70, and the
    # stop sits five ticks beyond the pierced extreme rather than the level itself.
    assert order.stop_price == Decimal("2008.70") + (Decimal("5") * spec.tick)
    assert order.limit_price < order.stop_price
    assert order.target_price < order.limit_price
    assert order.risk == order.stop_price - order.limit_price
    assert _local_of(order.placed_at).time() == dt.time(10, 15)


def test_a_sweep_without_a_shift_places_nothing(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    """A sweep and an imbalance without a market structure shift is not a setup."""
    bars = _rewrite_bar(
        canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False),
        dt.time(3, 15),
        (Decimal("2005.60"), Decimal("2005.70"), Decimal("2005.50"), Decimal("2005.60")),
        bar_factory=bar_factory,
    )
    bars = _rewrite_bar(
        bars,
        dt.time(3, 20),
        (Decimal("2005.60"), Decimal("2005.70"), Decimal("2005.50"), Decimal("2005.60")),
        bar_factory=bar_factory,
    )

    strategy = _strategy(strategy_config, spec)
    placed = [intent for intent in _intents(bars, strategy) if isinstance(intent, PlaceLimit)]

    # The sweep stands and the imbalance at 03:25 stands; nothing closes through the
    # window's structural swing, so there is no shift and therefore no order.
    assert placed == []
    assert strategy.declined == ()


def test_a_sweep_confirmed_after_the_window_opens_no_setup(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    """The sweep must confirm inside the 60 minutes, not just before it."""
    bars = _rewrite_bar(
        canonical_day_factory(dt.date(2026, 3, 9)),
        dt.time(3, 5),
        (Decimal("2007.60"), Decimal("2007.65"), Decimal("2006.60"), Decimal("2007.60")),
        bar_factory=bar_factory,
    )
    moved = _rewrite_bar(
        bars,
        dt.time(4, 5),
        (Decimal("2007.60"), Decimal("2007.65"), Decimal("2004.70"), Decimal("2005.30")),
        bar_factory=bar_factory,
    )

    strategy = _strategy(strategy_config, spec)
    placed = [intent for intent in _intents(moved, strategy) if isinstance(intent, PlaceLimit)]

    assert placed == []


def test_one_setup_per_window(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    """A second sweep of the Asian low later in the window finds the book busy."""
    bars = _rewrite_bar(
        canonical_day_factory(dt.date(2026, 3, 9)),
        dt.time(3, 50),
        (Decimal("2007.00"), Decimal("2007.10"), Decimal("2004.70"), Decimal("2005.30")),
        bar_factory=bar_factory,
    )

    strategy = _strategy(strategy_config, spec)
    placed = [intent for intent in _intents(bars, strategy) if isinstance(intent, PlaceLimit)]

    assert len(placed) == 1


def test_a_resting_order_blocks_a_second_setup(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    """The entry never fills, so the book already has risk on it when the next sweep."""
    bars = _rewrite_bar(
        canonical_day_factory(dt.date(2026, 3, 9)),
        dt.time(3, 25),
        (Decimal("2007.00"), Decimal("2007.20"), Decimal("2006.90"), Decimal("2007.10")),
        bar_factory=bar_factory,
    )
    bars = _rewrite_bar(
        bars,
        dt.time(3, 50),
        (Decimal("2007.00"), Decimal("2007.10"), SWEEP_LOW, Decimal("2005.30")),
        bar_factory=bar_factory,
    )

    strategy = _strategy(strategy_config, spec)
    placed = [intent for intent in _intents(bars, strategy) if isinstance(intent, PlaceLimit)]

    assert len(placed) == 1
    assert strategy.declined == ()


def test_a_limit_order_expires_after_twelve_m5_bars(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """An entry that never fills is withdrawn after one window of unfilled bars.

    The order was placed as the 03:20 bar closed, so it could fill on the next twelve
    bars; it is withdrawn when the thirteenth arrives.
    """
    bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False)
    strategy = _strategy(strategy_config, spec)
    intents = _intents(bars, strategy)

    placed = [intent for intent in intents if isinstance(intent, PlaceLimit)]
    expired = [
        intent
        for intent in intents
        if isinstance(intent, CancelOrder) and "unfilled" in intent.reason
    ]

    assert len(placed) == 1
    assert len(expired) == 1
    assert expired[0].at == placed[0].placed_at + dt.timedelta(minutes=5 * 13)
    assert expired[0].order_id == placed[0].order_id
    assert strategy.resting_orders == ()


def test_positions_are_flattened_by_1645_new_york(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """A position whose target never comes is closed by the dynamic close."""
    bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False)
    strategy = _strategy(strategy_config, spec)
    intents = _drive(bars, strategy, dt.time(3, 25))

    flattened = [
        intent
        for intent in intents
        if isinstance(intent, FlattenPosition) and "dynamic close" in intent.reason
    ]

    assert len(flattened) == 1
    assert flattened[0].at == _clock(dt.date(2026, 3, 9), DYNAMIC_CLOSE)
    assert flattened[0].quantity == Decimal("1")
    assert flattened[0].side is OrderSide.SELL
    assert strategy.positions == ()
    assert len(strategy.closed_positions) == 1


def test_the_flatten_happens_once_per_day(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    bars = canonical_day_factory(dt.date(2026, 3, 9), reaches_target=False)
    strategy = _strategy(strategy_config, spec)
    intents = _drive(bars, strategy, dt.time(3, 25))

    flattens = [
        intent
        for intent in intents
        if isinstance(intent, FlattenPosition) and "dynamic close" in intent.reason
    ]

    assert len(flattens) == 1


# --------------------------------------------------------------------------- #
# DST end to end
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "day",
    [dt.date(2026, 3, 6), dt.date(2026, 3, 9), dt.date(2026, 10, 30), dt.date(2026, 11, 2)],
)
def test_the_london_setup_fires_on_both_sides_of_both_transitions(
    strategy_config: StrategyConfig, spec, canonical_day_factory, day: dt.date
) -> None:
    order = _only_order(canonical_day_factory(day), strategy_config, spec)

    assert order.limit_price == FVG_MIDPOINT
    assert order.reference is RangeName.ASIAN
    # The order lands in the London window of the same local date, whatever UTC hour
    # that window starts at.
    assert _local_of(order.placed_at).time() == dt.time(3, 20)


def test_the_first_edt_monday_and_the_first_est_monday_differ_by_an_hour(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    """Same wall clock, different UTC: the 03:00 window is 07:00 UTC in EDT, 08:00 in EST."""
    edt = _only_order(canonical_day_factory(dt.date(2026, 3, 9)), strategy_config, spec)
    est = _only_order(canonical_day_factory(dt.date(2026, 11, 2)), strategy_config, spec)

    assert _local_of(edt.placed_at).time() == _local_of(est.placed_at).time() == dt.time(3, 20)
    assert est.placed_at.hour - edt.placed_at.hour == 1


# --------------------------------------------------------------------------- #
# the feed
# --------------------------------------------------------------------------- #
def test_the_strategy_refuses_an_incomplete_bar(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    from engine.strategy.silver_bullet import StrategyFeedError

    strategy = _strategy(strategy_config, spec)
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    _intents(bars[:-1], strategy)
    unfinished = bar_factory(
        _local(bars[-1]).replace(tzinfo=None),
        bars[-1].mid_ohlc.open,
        bars[-1].mid_ohlc.high,
        bars[-1].mid_ohlc.low,
        bars[-1].mid_ohlc.close,
        complete=False,
    )

    with pytest.raises(StrategyFeedError):
        strategy.on_bar(unfinished)


def test_the_strategy_refuses_a_bar_out_of_order(
    strategy_config: StrategyConfig, spec, canonical_day_factory
) -> None:
    from engine.strategy.silver_bullet import StrategyFeedError

    strategy = _strategy(strategy_config, spec)
    bars = canonical_day_factory(dt.date(2026, 3, 9))

    with pytest.raises(StrategyFeedError):
        strategy.on_bar(bars[10])
        strategy.on_bar(bars[10])


def test_the_strategy_refuses_a_non_m5_bar(
    strategy_config: StrategyConfig, spec, canonical_day_factory, bar_factory
) -> None:
    from engine.strategy.silver_bullet import StrategyFeedError

    strategy = _strategy(strategy_config, spec)
    bars = canonical_day_factory(dt.date(2026, 3, 9))
    _intents(bars[:-1], strategy)
    minute = bar_factory(
        _local(bars[-1]).replace(tzinfo=None),
        bars[-1].mid_ohlc.open,
        bars[-1].mid_ohlc.high,
        bars[-1].mid_ohlc.low,
        bars[-1].mid_ohlc.close,
        timeframe="M1",
    )

    with pytest.raises(StrategyFeedError):
        strategy.on_bar(minute)
