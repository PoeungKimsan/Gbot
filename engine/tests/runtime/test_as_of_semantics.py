"""Tests for as-of semantics and separated signal/execute phases (VAR_117).

Verifies:
1. Signal phase (after close on t): refresh, validate, compute, persist signal and journal it.
2. Execute phase (next session ~15:50-15:55 ET): load signal, require
   signal_date == last completed session, submit orders.
3. Deterministic client_order_id keyed on (signal_date, symbol).
4. Tests with patched clocks and synthetic bars:
   - Fri 2026-10-30 16:30 ET (signal ok)
   - Mon 2026-11-02 15:50 ET with data through 10-30 (executes ok)
   - Tue 2026-11-03 15:50 ET with data through 10-30 (raises stale)
   - Post-holiday Monday
   - Saturday
"""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from engine.execution.alpaca_broker import AlpacaBroker
from engine.feed.alpaca_feed import AlpacaFeed
from engine.feed.refresh_data import generate_manifest
from engine.market.nyse_calendar import (
    get_nyse_trading_days,
    is_last_trading_day_of_month,
    last_completed_session,
)
from engine.runtime.live_trader import LiveTrader

NEW_YORK = ZoneInfo("America/New_York")
UNIVERSE = (
    "SPY", "QQQ", "IWM", "EFA", "EEM", "IEF", "TLT", "SHY", "LQD", "VNQ", "GLD", "DBC"
)


def _build_synthetic_dataset(target_dir: Path, end_date: dt.date, num_bars: int = 260) -> None:
    """Generate synthetic ETF CSV files with valid NYSE dates ending at end_date."""
    target_dir.mkdir(parents=True, exist_ok=True)
    start_search = end_date - dt.timedelta(days=num_bars * 2)
    all_trading_days = get_nyse_trading_days(start_search, end_date)
    trading_days = all_trading_days[-num_bars:]
    assert trading_days[-1] == end_date

    for sym_idx, sym in enumerate(UNIVERSE):
        csv_file = target_dir / f"{sym}_daily.csv"
        rows: list[dict[str, str]] = []
        base_price = Decimal(100 + sym_idx * 10)
        for i, d in enumerate(trading_days):
            price = (base_price + Decimal(i) * Decimal("0.10")).quantize(Decimal("0.01"))
            rows.append({
                "Date": d.isoformat(),
                "Close": str(price),
                "Adj Close": str(price),
            })
        with csv_file.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["Date", "Close", "Adj Close"])
            writer.writeheader()
            writer.writerows(rows)

    generate_manifest(target_dir=target_dir, universe=UNIVERSE)


@pytest.fixture
def synthetic_data_oct2026(tmp_path: Path) -> Path:
    data_dir = tmp_path / "etf_cache"
    _build_synthetic_dataset(data_dir, end_date=dt.date(2026, 10, 30), num_bars=260)
    return data_dir


def test_calendar_helpers_oct_nov_2026() -> None:
    # Fri 2026-10-30 16:30 ET
    t_fri_close = dt.datetime(2026, 10, 30, 16, 30, tzinfo=NEW_YORK)
    assert last_completed_session(t_fri_close) == dt.date(2026, 10, 30)
    assert is_last_trading_day_of_month(dt.date(2026, 10, 30)) is True

    # Mon 2026-11-02 15:50 ET (before close)
    t_mon_before_close = dt.datetime(2026, 11, 2, 15, 50, tzinfo=NEW_YORK)
    assert last_completed_session(t_mon_before_close) == dt.date(2026, 10, 30)
    assert is_last_trading_day_of_month(dt.date(2026, 11, 2)) is False

    # Tue 2026-11-03 15:50 ET (after Mon closed, before Tue close)
    t_tue_before_close = dt.datetime(2026, 11, 3, 15, 50, tzinfo=NEW_YORK)
    assert last_completed_session(t_tue_before_close) == dt.date(2026, 11, 2)


def test_last_completed_session_post_holiday_monday() -> None:
    # Labor Day 2026: Monday 2026-09-07 is a market holiday
    # On Labor Day afternoon:
    t_holiday = dt.datetime(2026, 9, 7, 14, 0, tzinfo=NEW_YORK)
    assert last_completed_session(t_holiday) == dt.date(2026, 9, 4)  # Previous Friday

    # On Tuesday morning 2026-09-08 10:00 ET (market open, session in-progress):
    t_tue_morning = dt.datetime(2026, 9, 8, 10, 0, tzinfo=NEW_YORK)
    assert last_completed_session(t_tue_morning) == dt.date(2026, 9, 4)

    # After Tuesday close 2026-09-08 16:15 ET:
    t_tue_after_close = dt.datetime(2026, 9, 8, 16, 15, tzinfo=NEW_YORK)
    assert last_completed_session(t_tue_after_close) == dt.date(2026, 9, 8)


def test_last_completed_session_saturday() -> None:
    # Saturday 2026-10-31 12:00 ET
    t_sat = dt.datetime(2026, 10, 31, 12, 0, tzinfo=NEW_YORK)
    assert last_completed_session(t_sat) == dt.date(2026, 10, 30)


@pytest.mark.asyncio
async def test_signal_phase_friday_after_close(synthetic_data_oct2026: Path) -> None:
    feed = AlpacaFeed(data_dir=synthetic_data_oct2026)
    broker = AlpacaBroker(mock=True)
    t_fri = dt.datetime(2026, 10, 30, 16, 30, tzinfo=NEW_YORK)
    signal_file = synthetic_data_oct2026 / "latest_signal.json"

    trader = LiveTrader(
        feed=feed,
        broker=broker,
        enable_journal=False,
        current_time=t_fri,
    )

    sig = trader.generate_and_persist_signal(now_ny=t_fri, signal_file=signal_file)
    assert sig.as_of_date == dt.date(2026, 10, 30)
    assert signal_file.is_file()
    assert len(sig.top_assets) == 3


@pytest.mark.asyncio
async def test_execute_phase_next_session_monday(synthetic_data_oct2026: Path) -> None:
    feed = AlpacaFeed(data_dir=synthetic_data_oct2026)
    broker = AlpacaBroker(mock=True)
    t_fri = dt.datetime(2026, 10, 30, 16, 30, tzinfo=NEW_YORK)
    signal_file = synthetic_data_oct2026 / "latest_signal.json"

    trader = LiveTrader(
        feed=feed,
        broker=broker,
        enable_journal=False,
        current_time=t_fri,
    )
    trader.generate_and_persist_signal(now_ny=t_fri, signal_file=signal_file)

    # Monday 2026-11-02 15:50 ET: execute phase
    t_mon = dt.datetime(2026, 11, 2, 15, 50, tzinfo=NEW_YORK)
    orders = await trader.execute_persisted_signal(now_ny=t_mon, signal_file=signal_file)
    assert len(orders) > 0
    for o in orders:
        assert o["client_order_id"].startswith("var117-2026-10-30-")


@pytest.mark.asyncio
async def test_execute_phase_tuesday_raises_stale(synthetic_data_oct2026: Path) -> None:
    feed = AlpacaFeed(data_dir=synthetic_data_oct2026)
    broker = AlpacaBroker(mock=True)
    t_fri = dt.datetime(2026, 10, 30, 16, 30, tzinfo=NEW_YORK)
    signal_file = synthetic_data_oct2026 / "latest_signal.json"

    trader = LiveTrader(
        feed=feed,
        broker=broker,
        enable_journal=False,
        current_time=t_fri,
    )
    trader.generate_and_persist_signal(now_ny=t_fri, signal_file=signal_file)

    # Tuesday 2026-11-03 15:50 ET: Monday 11-02 already closed, so signal from 10-30 is stale!
    t_tue = dt.datetime(2026, 11, 3, 15, 50, tzinfo=NEW_YORK)
    with pytest.raises(ValueError, match="Stale signal"):
        await trader.execute_persisted_signal(now_ny=t_tue, signal_file=signal_file)
