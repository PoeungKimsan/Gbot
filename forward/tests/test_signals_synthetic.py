"""Synthetic data tests for T2 and T3 signal and execution logic."""

import datetime as dt

import pytest
from research.trend.models import DailyBar
from research.trend.strategies import run_t2_sma_cross, run_t3_tsmom


def make_synthetic_bars(
    n: int = 300,
    base_price: float = 100.0,
    trend: float = 0.0,
    vol: float = 1.0,
    start_date: dt.date = dt.date(2020, 1, 1),
) -> list[DailyBar]:
    bars: list[DailyBar] = []
    p = base_price
    cur_date = start_date

    for i in range(n):
        p += trend
        op = p
        hi = p + vol
        lo = p - vol
        cl = p + (vol * 0.1)

        # Skip weekends
        while cur_date.weekday() in (5, 6):
            cur_date += dt.timedelta(days=1)

        bars.append(
            DailyBar(
                date=cur_date,
                open=op,
                high=hi,
                low=lo,
                close=cl,
                volume=1000,
                atr20=2.0 * vol,
                sma50=p - 5.0,
                sma200=p,
                donchian_high55=p + 5.0,
                donchian_low55=p - 5.0,
                donchian_high20=p + 2.0,
                donchian_low20=p - 2.0,
                return_252=0.05,
                is_month_end=False,
            )
        )
        cur_date += dt.timedelta(days=1)

    return bars


def test_t2_synthetic_bullish_cross_entry():
    """Verify that a bullish 50/200 cross triggers a long trade at next bar Open."""
    bars = make_synthetic_bars(n=260, base_price=100.0, vol=1.0)
    # Configure single clean cross at bar 219:
    # bars[:219] have sma50 = 95 < sma200 = 100
    for k in range(219, len(bars)):
        bars[k].sma50 = 105.0
        bars[k].sma200 = 100.0

    trades = run_t2_sma_cross("EURUSD", bars, start_date=bars[0].date, end_date=bars[-1].date)
    assert len(trades) >= 1
    t = trades[0]
    assert t.direction == "LONG"
    assert t.entry_date == bars[220].date
    assert t.entry_price == bars[220].open
    assert t.stop_distance == pytest.approx(3.0 * bars[219].atr20)
    assert t.stop_loss == pytest.approx(bars[220].open - 3.0 * bars[219].atr20)


def test_t2_synthetic_stop_loss_hit():
    """Verify that breaching stop loss triggers stop exit with correct penalty."""
    bars = make_synthetic_bars(n=260, base_price=100.0, vol=1.0)
    # Trigger long at bar 220
    for k in range(219, len(bars)):
        bars[k].sma50 = 105.0
        bars[k].sma200 = 100.0

    # Stop distance is 3 * atr = 3 * 2.0 = 6.0. Entry price ~ 100. Stop is at 94.0.
    # Force bar 230 to crash through stop
    stop_price = bars[220].open - 6.0
    bars[230].low = stop_price - 2.0
    bars[230].close = stop_price - 1.0

    trades = run_t2_sma_cross("EURUSD", bars, start_date=bars[0].date, end_date=bars[-1].date)
    assert len(trades) >= 1
    t = trades[0]
    assert t.exit_reason == "STOP"
    assert t.is_stop is True
    assert t.exit_date == bars[230].date
    assert t.exit_price == pytest.approx(stop_price)


def test_t3_synthetic_month_end_sign():
    """Verify that T3 triggers on month-end sign and persists across same-sign months."""
    bars = make_synthetic_bars(n=300, base_price=100.0, vol=1.0)
    # Set month-end at bar 255
    bars[255].is_month_end = True
    bars[255].return_252 = 0.10
    # Next month-end at bar 275 also positive
    bars[275].is_month_end = True
    bars[275].return_252 = 0.12

    trades = run_t3_tsmom("EURUSD", bars, start_date=bars[0].date, end_date=bars[-1].date)
    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "LONG"
    assert t.entry_date == bars[256].date  # Enters next bar Open
