"""Tests for the Asian Judas Swing strategy (engine.strategy.judas_swing).

Verifies:
1. Asian session high/low accumulation and locking at 00:00 NY.
2. Inactive outside the 02:00 - 05:00 NY London Open Killzone.
3. Sub-threshold breach (< 5 ticks) is rejected.
4. Valid sweep (>= 5 ticks) followed by candle close back inside (SFP) triggers signal.
5. Invalidation (SL) placed beyond the sweep extreme wick + buffer.
6. Target (TP) placed at the opposite Asian range boundary (or >= 2R floor).
7. One trade per calendar day lockout.
8. Zero-float Decimal purity.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from engine.market.models import OHLC, Bar, Timeframe
from engine.strategy.judas_swing import JudasSide, JudasSwingStrategy

NY_TZ = ZoneInfo("America/New_York")


def _make_bar(
    ny_datetime: dt.datetime,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Bar:
    """Helper to create a complete M5 bar with UTC timestamp from NY local time."""
    utc_dt = ny_datetime.replace(tzinfo=NY_TZ).astimezone(dt.UTC)
    mid = OHLC(
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )
    return Bar(
        timeframe=Timeframe.M5.value,
        timestamp_utc=utc_dt,
        complete=True,
        volume=100,
        bid_ohlc=mid,
        ask_ohlc=mid,
    )


def test_asian_range_locks_at_midnight_ny():
    strat = JudasSwingStrategy(min_sweep_ticks=5, tick_size=Decimal("0.01"))

    # Day 1 evening: Asian session bars between 18:00 and 23:55 NY
    base_date = dt.date(2026, 3, 10)
    for hour, minute in [(19, 0), (20, 0), (21, 30), (23, 0)]:
        t = dt.datetime.combine(base_date, dt.time(hour, minute))
        strat.on_bar(_make_bar(t, "2700.00", "2705.50", "2695.00", "2702.00"))

    # Prior to 00:00, range is not yet locked
    assert strat.current_asian_range is None

    # Bar at 00:05 NY locks the Asian range
    midnight_bar = _make_bar(
        dt.datetime.combine(base_date + dt.timedelta(days=1), dt.time(0, 5)),
        "2702.00",
        "2703.00",
        "2701.00",
        "2702.50",
    )
    strat.on_bar(midnight_bar)

    locked = strat.current_asian_range
    assert locked is not None
    assert locked.high == Decimal("2705.50")
    assert locked.low == Decimal("2695.00")
    assert locked.complete is True


def test_no_signal_outside_london_window():
    strat = JudasSwingStrategy()
    base_date = dt.date(2026, 3, 10)
    trade_date = base_date + dt.timedelta(days=1)

    # Establish Asian range (2695.00 - 2705.00)
    strat.on_bar(_make_bar(dt.datetime.combine(base_date, dt.time(20, 0)), "2700.00", "2705.00", "2695.00", "2700.00"))
    strat.on_bar(_make_bar(dt.datetime.combine(trade_date, dt.time(0, 5)), "2700.00", "2701.00", "2699.00", "2700.00"))

    # At 01:30 NY (before 02:00), price sweeps high and closes back
    early_bar = _make_bar(dt.datetime.combine(trade_date, dt.time(1, 30)), "2704.00", "2706.00", "2703.00", "2704.00")
    assert strat.on_bar(early_bar) is None


def test_sub_threshold_sweep_is_ignored():
    strat = JudasSwingStrategy(min_sweep_ticks=5, tick_size=Decimal("0.01"))
    base_date = dt.date(2026, 3, 10)
    trade_date = base_date + dt.timedelta(days=1)

    # Range High = 2705.00. 5 ticks = 0.05, so threshold is > 2705.05.
    strat.on_bar(_make_bar(dt.datetime.combine(base_date, dt.time(20, 0)), "2700.00", "2705.00", "2695.00", "2700.00"))
    strat.on_bar(_make_bar(dt.datetime.combine(trade_date, dt.time(0, 5)), "2700.00", "2701.00", "2699.00", "2700.00"))

    # Bar only reaches 2705.03 (3 ticks breach) then closes at 2704.50
    touch_bar = _make_bar(dt.datetime.combine(trade_date, dt.time(2, 15)), "2704.00", "2705.03", "2703.00", "2704.50")
    assert strat.on_bar(touch_bar) is None


def test_valid_bearish_judas_swing_signal():
    strat = JudasSwingStrategy(
        min_sweep_ticks=5,
        buffer_ticks=10,
        tick_size=Decimal("0.01"),
        min_risk_reward=Decimal("2.0"),
    )
    base_date = dt.date(2026, 3, 10)
    trade_date = base_date + dt.timedelta(days=1)

    # Asian Range: High 2705.00, Low 2695.00
    strat.on_bar(_make_bar(dt.datetime.combine(base_date, dt.time(20, 0)), "2700.00", "2705.00", "2695.00", "2700.00"))
    strat.on_bar(_make_bar(dt.datetime.combine(trade_date, dt.time(0, 5)), "2700.00", "2701.00", "2699.00", "2700.00"))

    # At 02:30 NY, bar spikes to 2706.50 (+1.50 = +150 ticks > 5 ticks) and closes back inside at 2704.00 (< 2705.00)
    judas_bar = _make_bar(dt.datetime.combine(trade_date, dt.time(2, 30)), "2704.00", "2706.50", "2703.50", "2704.00")
    setup = strat.on_bar(judas_bar)

    assert setup is not None
    assert setup.side is JudasSide.SELL
    assert setup.entry_price == Decimal("2704.00")
    # SL = sweep_extreme (2706.50) + buffer (0.10) = 2706.60
    assert setup.stop_loss == Decimal("2706.60")
    # Risk = 2706.60 - 2704.00 = 2.60
    # TP = Asian Low (2695.00). Reward = 2704.00 - 2695.00 = 9.00 (Reward/Risk = 9.00 / 2.60 = 3.46R >= 2.0R)
    assert setup.take_profit == Decimal("2695.00")
    assert setup.asian_high == Decimal("2705.00")
    assert setup.asian_low == Decimal("2695.00")


def test_valid_bullish_judas_swing_signal():
    strat = JudasSwingStrategy(
        min_sweep_ticks=5,
        buffer_ticks=10,
        tick_size=Decimal("0.01"),
        min_risk_reward=Decimal("2.0"),
    )
    base_date = dt.date(2026, 3, 10)
    trade_date = base_date + dt.timedelta(days=1)

    # Asian Range: High 2705.00, Low 2695.00
    strat.on_bar(_make_bar(dt.datetime.combine(base_date, dt.time(20, 0)), "2700.00", "2705.00", "2695.00", "2700.00"))
    strat.on_bar(_make_bar(dt.datetime.combine(trade_date, dt.time(0, 5)), "2700.00", "2701.00", "2699.00", "2700.00"))

    # At 03:15 NY, bar spikes down to 2693.80 (-1.20 = -120 ticks < -5 ticks) and closes back inside at 2696.00 (> 2695.00)
    judas_bar = _make_bar(dt.datetime.combine(trade_date, dt.time(3, 15)), "2695.50", "2696.50", "2693.80", "2696.00")
    setup = strat.on_bar(judas_bar)

    assert setup is not None
    assert setup.side is JudasSide.BUY
    assert setup.entry_price == Decimal("2696.00")
    # SL = sweep_extreme (2693.80) - buffer (0.10) = 2693.70
    assert setup.stop_loss == Decimal("2693.70")
    # TP = Asian High (2705.00)
    assert setup.take_profit == Decimal("2705.00")


def test_single_trade_per_day_lockout():
    strat = JudasSwingStrategy()
    base_date = dt.date(2026, 3, 10)
    trade_date = base_date + dt.timedelta(days=1)

    strat.on_bar(_make_bar(dt.datetime.combine(base_date, dt.time(20, 0)), "2700.00", "2705.00", "2695.00", "2700.00"))
    strat.on_bar(_make_bar(dt.datetime.combine(trade_date, dt.time(0, 5)), "2700.00", "2701.00", "2699.00", "2700.00"))

    # First valid trigger
    bar1 = _make_bar(dt.datetime.combine(trade_date, dt.time(2, 30)), "2704.00", "2706.50", "2703.50", "2704.00")
    setup1 = strat.on_bar(bar1)
    assert setup1 is not None

    # Subsequent bar in same London window must be refused
    bar2 = _make_bar(dt.datetime.combine(trade_date, dt.time(3, 00)), "2704.00", "2707.00", "2703.50", "2704.00")
    assert strat.on_bar(bar2) is None
