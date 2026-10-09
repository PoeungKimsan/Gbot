"""Tests for the closed-bar structure detectors.

The invariant that organises this file is that **a signal never changes once it has
been emitted**. Everything else follows from it: a swing point needs its full right
side before it exists, a level cannot be swept before it is knowable, and an
incomplete bar contributes nothing because it has no final close.

Each detector is also exercised through two paths -- a *streaming* engine that folds
one bar at a time and a *batch* function that scans a whole list by index. If they
disagree at any prefix, the backtest is repainting, so the suite compares them bar
by bar rather than trusting either.
"""

import dataclasses
import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from engine.market.models import Bar
from engine.strategy.config import StrategyConfig
from engine.strategy.detectors import (
    FRACTAL_SOURCE,
    Level,
    StructureDetector,
    StructureSignals,
    SwingKind,
    atr_series,
    swing_series,
)

NEW_YORK = ZoneInfo("America/New_York")

#: A swing high of 2005.20, its two-bar shoulders either side, then a long decline so
#: no later swing forms and the peak stays the most recent one.
PYRAMID: tuple[str, ...] = (
    "2000.00",
    "2001.00",
    "2002.00",
    "2005.00",
    "2002.00",
    "2001.90",
    "2001.80",
    "2001.70",
    "2001.60",
    "2001.50",
    "2001.40",
    "2001.30",
    "2001.20",
    "2001.10",
)

BASE: dt.datetime = dt.datetime(2026, 3, 9, 9, 0, tzinfo=dt.UTC)
WIGGLE: Decimal = Decimal("0.20")


def _detector(strategy_config: StrategyConfig, **changes: object) -> StructureDetector:
    """A detector, optionally over one configuration value."""
    config = strategy_config
    if changes:
        config = dataclasses.replace(strategy_config, **changes)
    return StructureDetector(config=config, tick=Decimal("0.01"))


def _feed(detector: StructureDetector, bars: list[Bar]) -> list[StructureSignals]:
    return [detector.on_bar(bar) for bar in bars]


def _m5(index: int) -> dt.datetime:
    """A New York-side local instant, ``index`` M5 bars after the base."""
    return dt.datetime(2026, 3, 9, 9, 0) + dt.timedelta(minutes=5 * index)


def _utc(index: int) -> dt.datetime:
    """The same instant, resolved through the pinned New York zone.

    March 9 is the first EDT morning of 2026, so 09:00 local is 13:00 UTC. Getting
    this wrong is how a session test ends up silently checking EST.
    """
    return _m5(index).replace(tzinfo=NEW_YORK).astimezone(dt.UTC)


def _pyramid_bar(bar_factory, index: int, price: str, *, complete: bool = True) -> Bar:
    """One bar whose whole range sits ``WIGGLE`` either side of ``price``."""
    level = Decimal(price)
    return bar_factory(
        _m5(index), level, level + WIGGLE, level - WIGGLE, level, complete=complete
    )


def _level(price: str, *, kind: SwingKind, known_at: int, ts: dt.datetime, source: str) -> Level:
    return Level(
        kind=kind,
        price=Decimal(price),
        timestamp_utc=ts,
        known_at_index=known_at,
        source=source,
    )


def _bar(bar_factory, index: int, open_, high, low, close, *, complete: bool = True) -> Bar:
    return bar_factory(_m5(index), open_, high, low, close, complete=complete)


# --------------------------------------------------------------------------- #
# swings
# --------------------------------------------------------------------------- #
def test_a_pyramid_peak_is_a_swing_high(strategy_config: StrategyConfig, bar_factory) -> None:
    detector = _detector(strategy_config)
    signals = _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)])

    assert len(detector.swings) == 1
    swing = detector.swings[0]
    assert swing.kind is SwingKind.HIGH
    assert swing.price == Decimal("2005.20")
    assert swing.index == 3
    # Confirmed two bars to the right, not when it printed.
    assert swing.confirmed_index == 5
    assert swing.confirmed_at == _utc(5)
    assert not any(signal.swings for signal in signals[:5])
    assert len(signals[5].swings) == 1


def test_a_peak_without_its_right_side_is_not_a_swing_yet(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)

    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID[:5])])

    assert detector.swings == ()


def test_swing_n_widens_the_required_shoulder(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config, swing_n=3)

    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID[:6])])

    assert detector.swings == ()


def test_a_pyramid_valley_is_a_swing_low(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    prices = ("2005.00", "2004.00", "2003.00", "2000.00", "2003.00", "2004.00")

    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(prices)])

    assert [swing.kind for swing in detector.swings] == [SwingKind.LOW]
    assert detector.swings[0].price == Decimal("1999.80")


def test_equal_highs_are_not_a_swing(strategy_config: StrategyConfig, bar_factory) -> None:
    detector = _detector(strategy_config)
    bars = [_bar(bar_factory, i, "2002.00", "2005.00", "2000.00", "2002.00") for i in range(7)]

    _feed(detector, bars)

    assert detector.swings == ()


def test_an_incomplete_bar_contributes_nothing(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID[:5])])

    detector.on_bar(_pyramid_bar(bar_factory, 5, PYRAMID[5], complete=False))

    assert detector.bar_count == 5
    assert detector.swings == ()


# --------------------------------------------------------------------------- #
# ATR
# --------------------------------------------------------------------------- #
def test_atr_is_absent_until_the_window_fills(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID[:3])])

    assert detector.atr is None

    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID[3:], start=3)])

    assert detector.atr is not None


def test_a_gap_away_from_the_previous_close_widens_the_true_range(bar_factory) -> None:
    bars = [
        _bar(bar_factory, 0, "1999.50", "2000.00", "1999.00", "1999.50"),
        _bar(bar_factory, 1, "2005.00", "2010.00", "2005.00", "2010.00"),
    ]

    series = atr_series(bars, window=1)

    # The first bar's true range is its range. The second reaches up to
    # 2010 - 1999.50, not merely 2005 - 2010, because the close gapped away.
    assert series == [Decimal("1.00"), Decimal("10.50")]


def test_streamed_atr_matches_the_batch_series(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    bars = [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)]
    bars += [
        _bar(bar_factory, i, "2003.00", "2004.00", "2001.50", "2003.20")
        for i in range(len(PYRAMID), len(PYRAMID) + 10)
    ]
    detector = _detector(strategy_config)
    streamed: list[Decimal | None] = []
    for bar in bars:
        detector.on_bar(bar)
        streamed.append(detector.atr)

    assert streamed == atr_series(bars, window=strategy_config.atr_window)


# --------------------------------------------------------------------------- #
# liquidity sweeps
# --------------------------------------------------------------------------- #
def test_a_breach_that_closes_back_inside_is_a_sweep(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    detector.add_level(_level("2005.00", kind=SwingKind.LOW, known_at=0, ts=_m5(0), source="ASIAN"))

    signals = _feed(detector, [_bar(bar_factory, 0, "2006.00", "2006.00", "2004.70", "2005.30")])

    assert len(signals[0].sweeps) == 1
    sweep = signals[0].sweeps[0]
    assert sweep.level.price == Decimal("2005.00")
    assert sweep.breach_price == Decimal("2004.70")
    assert sweep.close_back_price == Decimal("2005.30")
    assert sweep.close_back_price > sweep.level.price
    assert sweep.direction.value == "LONG"


def test_a_breach_below_the_threshold_is_not_a_sweep(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    detector.add_level(_level("2005.00", kind=SwingKind.LOW, known_at=0, ts=_m5(0), source="ASIAN"))

    signals = _feed(detector, [_bar(bar_factory, 0, "2005.00", "2005.00", "2004.98", "2004.99")])

    assert signals[0].sweeps == ()


def test_a_close_back_on_a_later_bar_still_confirms(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    detector.add_level(_level("2005.00", kind=SwingKind.LOW, known_at=0, ts=_m5(0), source="ASIAN"))

    signals = _feed(
        detector,
        [
            _bar(bar_factory, 0, "2006.00", "2006.00", "2004.50", "2004.60"),
            _bar(bar_factory, 1, "2004.60", "2005.20", "2004.40", "2005.10"),
        ],
    )

    assert signals[0].sweeps == ()
    assert len(signals[1].sweeps) == 1
    assert signals[1].sweeps[0].close_back_price == Decimal("2005.10")


def test_a_breach_that_never_closes_back_is_not_a_sweep(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    detector.add_level(_level("2005.00", kind=SwingKind.LOW, known_at=0, ts=_m5(0), source="ASIAN"))

    signals = _feed(
        detector,
        [
            _bar(bar_factory, 0, "2006.00", "2006.00", "2004.50", "2004.60"),
            _bar(bar_factory, 1, "2004.60", "2004.70", "2004.30", "2004.40"),
            _bar(bar_factory, 2, "2004.40", "2004.60", "2004.20", "2004.30"),
        ],
    )

    assert [signal.sweeps for signal in signals] == [(), (), ()]


def test_a_level_is_untouchable_before_it_is_knowable(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    """A range that only concludes at 05:00 cannot be swept by a 03:00 bar."""
    detector = _detector(strategy_config)
    detector.add_level(
        _level("2005.00", kind=SwingKind.LOW, known_at=3, ts=_m5(3), source="LONDON")
    )

    early = _feed(
        detector,
        [
            _bar(bar_factory, 0, "2006.00", "2006.00", "2004.50", "2004.60"),
            _bar(bar_factory, 1, "2004.60", "2004.70", "2004.20", "2004.30"),
            _bar(bar_factory, 2, "2004.30", "2004.50", "2004.10", "2004.20"),
        ],
    )
    known = _feed(detector, [_bar(bar_factory, 3, "2004.70", "2005.50", "2004.70", "2005.20")])

    # The first three bars trade straight through the level and are not allowed to
    # see it; the fourth arrives after the level was handed over, and sweeps it.
    assert [signal.sweeps for signal in early] == [(), (), ()]
    assert len(known[0].sweeps) == 1
    assert known[0].sweeps[0].level.source == "LONDON"
    assert known[0].sweeps[0].confirmed_index == 3


def test_sweeping_a_high_is_a_bearish_signal(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    detector.add_level(
        _level("2010.00", kind=SwingKind.HIGH, known_at=0, ts=_m5(0), source="ASIAN")
    )

    signals = _feed(
        detector, [_bar(bar_factory, 0, "2010.05", "2010.30", "2009.85", "2009.95")]
    )

    assert len(signals[0].sweeps) == 1
    assert signals[0].sweeps[0].direction.value == "SHORT"
    assert signals[0].sweeps[0].depth == Decimal("0.30")


def test_a_fractal_swing_is_a_sweepable_level_of_its_own(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)])
    detector.add_level(
        _level("2000.00", kind=SwingKind.LOW, known_at=0, ts=_m5(0), source=FRACTAL_SOURCE)
    )

    signals = _feed(
        detector, [_bar(bar_factory, len(PYRAMID), "2000.20", "2000.20", "1999.50", "2000.10")]
    )

    assert len(signals[0].sweeps) == 1
    assert signals[0].sweeps[0].level.source == FRACTAL_SOURCE


# --------------------------------------------------------------------------- #
# market structure shifts
# --------------------------------------------------------------------------- #
def test_a_big_bodied_close_through_the_latest_swing_is_a_shift(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)])

    signals = _feed(
        detector, [_bar(bar_factory, len(PYRAMID), "2005.00", "2007.80", "2004.90", "2007.80")]
    )

    assert len(signals[0].shifts) == 1
    shift = signals[0].shifts[0]
    assert shift.direction.value == "LONG"
    assert shift.close_price == Decimal("2007.80")
    assert shift.broken.price == Decimal("2005.20")
    assert shift.body == Decimal("2.80")
    # The ATR quoted with the shift is the one in force on the bar that made it.
    assert shift.atr == detector.atr


def test_a_small_bodied_close_is_not_a_shift(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)])

    signals = _feed(
        detector, [_bar(bar_factory, len(PYRAMID), "2005.20", "2005.40", "2005.10", "2005.25")]
    )

    assert signals[0].shifts == ()


def test_structure_is_measured_from_the_most_recent_swing(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    """A close beyond an *older* swing, with a newer one still above it, is nothing."""
    prices = (
        "2000.00",
        "2001.00",
        "2002.00",
        "2005.00",
        "2002.00",
        "2001.50",
        "2001.00",
        "2002.00",
        "2006.00",
        "2003.00",
        "2002.50",
        "2002.00",
        "2001.50",
        "2001.00",
    )
    detector = _detector(strategy_config)
    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(prices)])

    latest = max(detector.swings, key=lambda swing: swing.index)
    assert latest.price == Decimal("2006.20")

    signals = _feed(
        detector, [_bar(bar_factory, len(prices), "2004.00", "2005.60", "2003.90", "2005.50")]
    )

    assert signals[0].shifts == ()


def test_a_shift_needs_the_atr_window(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _detector(strategy_config)
    _feed(detector, [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID[:5])])

    signals = _feed(detector, [_bar(bar_factory, 5, "2002.00", "2007.00", "2001.90", "2006.90")])

    assert detector.atr is None
    assert signals[0].shifts == ()


# --------------------------------------------------------------------------- #
# fair value gaps
# --------------------------------------------------------------------------- #
def _warm_gap_detector(strategy_config: StrategyConfig, bar_factory) -> StructureDetector:
    """A detector with a full ATR window of quiet, identical bars."""
    detector = _detector(strategy_config)
    _feed(
        detector,
        [_bar(bar_factory, i, "2000.25", "2000.50", "2000.00", "2000.25")
         for i in range(strategy_config.atr_window)],
    )
    return detector


def test_a_wide_imbalance_is_a_gap_with_a_midpoint(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _warm_gap_detector(strategy_config, bar_factory)
    start = strategy_config.atr_window

    signals = _feed(
        detector,
        [
            _bar(bar_factory, start, "2000.40", "2001.50", "2000.40", "2001.00"),
            _bar(bar_factory, start + 1, "2001.50", "2002.50", "2001.50", "2002.00"),
            _bar(bar_factory, start + 2, "2002.00", "2003.00", "2001.80", "2003.00"),
        ],
    )

    assert len(signals[2].gaps) == 1
    gap = signals[2].gaps[0]
    assert gap.direction.value == "LONG"
    assert gap.bottom == Decimal("2001.50")
    assert gap.top == Decimal("2001.80")
    assert gap.size == Decimal("0.30")
    assert gap.midpoint == Decimal("2001.65")


def test_a_sub_threshold_imbalance_is_not_a_gap(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _warm_gap_detector(strategy_config, bar_factory)
    start = strategy_config.atr_window

    signals = _feed(
        detector,
        [
            _bar(bar_factory, start, "2000.40", "2001.50", "2000.40", "2001.00"),
            _bar(bar_factory, start + 1, "2001.50", "2002.50", "2001.50", "2002.00"),
            _bar(bar_factory, start + 2, "2001.60", "2001.70", "2001.55", "2001.60"),
        ],
    )

    # A gap of 2001.60 - 2001.50 = one dime, against an ATR over half a point.
    assert signals[2].gaps == ()


def test_a_buyers_midpoint_never_rounds_in_their_favour(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _warm_gap_detector(strategy_config, bar_factory)
    start = strategy_config.atr_window

    signals = _feed(
        detector,
        [
            _bar(bar_factory, start, "2000.40", "2001.50", "2000.40", "2001.00"),
            _bar(bar_factory, start + 1, "2001.50", "2002.50", "2001.50", "2002.00"),
            _bar(bar_factory, start + 2, "2002.00", "2003.00", "2001.81", "2003.00"),
        ],
    )

    gap = signals[2].gaps[0]
    assert (gap.top + gap.bottom) == Decimal("4003.31")
    # 2001.655 is not a tradeable price, and a buyer is charged the worse side.
    assert gap.midpoint == Decimal("2001.66")


def test_a_sellers_midpoint_never_rounds_in_their_favour(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    detector = _warm_gap_detector(strategy_config, bar_factory)
    start = strategy_config.atr_window

    signals = _feed(
        detector,
        [
            _bar(bar_factory, start, "2001.00", "2001.50", "1999.40", "2000.10"),
            _bar(bar_factory, start + 1, "2000.10", "2000.50", "1999.00", "1999.20"),
            _bar(bar_factory, start + 2, "1999.05", "1999.09", "1998.80", "1998.95"),
        ],
    )

    gap = signals[2].gaps[0]
    assert gap.direction.value == "SHORT"
    assert gap.top == Decimal("1999.40")
    assert gap.bottom == Decimal("1999.09")
    # 1999.245 is not tradeable, and a seller is charged the worse side.
    assert gap.midpoint == Decimal("1999.24")


# --------------------------------------------------------------------------- #
# batch vs streaming (the no-repaint invariant)
# --------------------------------------------------------------------------- #
def test_the_swing_stream_matches_the_batch_scan(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    bars = [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)]
    bars += [
        _bar(bar_factory, i, "2003.00", "2006.00", "2001.00", "2004.00")
        for i in range(len(PYRAMID), len(PYRAMID) + 10)
    ]
    detector = _detector(strategy_config)
    streamed: list[tuple[object, ...]] = []
    for bar in bars:
        detector.on_bar(bar)
        streamed.append(detector.swings)

    for prefix in range(1, len(bars) + 1):
        assert streamed[prefix - 1] == swing_series(
            bars[:prefix], swing_n=strategy_config.swing_n
        )


def test_a_batch_scan_cannot_see_a_swing_the_stream_has_not_confirmed(
    strategy_config: StrategyConfig, bar_factory
) -> None:
    """The batch function must agree with the stream, bar by bar, not just in total."""
    bars = [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)]

    for prefix in range(1, len(bars) + 1):
        for swing in swing_series(bars[:prefix], swing_n=strategy_config.swing_n):
            assert swing.confirmed_index < prefix


@pytest.mark.parametrize("prefix", [1, 3, 5, 8, 14])
def test_the_atr_series_is_prefix_stable(
    strategy_config: StrategyConfig, bar_factory, prefix: int
) -> None:
    bars = [_pyramid_bar(bar_factory, i, p) for i, p in enumerate(PYRAMID)]

    series = atr_series(bars, window=strategy_config.atr_window)

    assert series[prefix - 1] == atr_series(
        bars[:prefix], window=strategy_config.atr_window
    )[-1]
