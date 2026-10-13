"""Tests for systematic execution and signal guards (VAR_117).

Verifies that the execute phase strictly refuses orders when:
1. The day is not an active NYSE trading session (e.g. Saturday).
2. The day is the same evening as signal calculation (same-day rebalance blocked).
3. The time is outside the regular [15:50, 15:58) ET implementation window.
4. The latching drawdown kill-switch is tripped.
5. The persisted signal contains symbols outside the strategy universe.
6. The persisted signal exceeds the 100% total allocation cap.
7. Signal phase is attempted on a non-month-end trading day without explicit override.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from engine.execution.alpaca_broker import AlpacaBroker
from engine.feed.alpaca_feed import AlpacaFeed
from engine.runtime.live_trader import (
    ExecutionRefused,
    LiveTrader,
    SignalRefused,
)

NEW_YORK = ZoneInfo("America/New_York")
T_FRI_CLOSE = dt.datetime(2026, 10, 30, 16, 30, tzinfo=NEW_YORK)


@pytest.fixture
def synthetic_oct2026_trader(
    tmp_path: Path, build_synthetic_dataset: Any
) -> tuple[LiveTrader, Path]:
    cache_dir = tmp_path / "etf_cache"
    build_synthetic_dataset(cache_dir, end_date=dt.date(2026, 10, 30), num_bars=260)
    feed = AlpacaFeed(data_dir=cache_dir)
    broker = AlpacaBroker(mock=True)
    trader = LiveTrader(
        feed=feed,
        broker=broker,
        enable_journal=False,
        current_time=T_FRI_CLOSE,
    )
    sig_file = tmp_path / "signal.json"
    trader.generate_and_persist_signal(now_ny=T_FRI_CLOSE, signal_file=sig_file)
    return trader, sig_file


@pytest.mark.asyncio
async def test_execute_guard_refuses_saturday(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, sig_file = synthetic_oct2026_trader
    t_sat = dt.datetime(2026, 10, 31, 12, 0, tzinfo=NEW_YORK)
    with pytest.raises(ExecutionRefused, match="not an active NYSE trading session"):
        await trader.execute_persisted_signal(now_ny=t_sat, signal_file=sig_file)


@pytest.mark.asyncio
async def test_execute_guard_refuses_same_day(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, sig_file = synthetic_oct2026_trader
    t_same_day = dt.datetime(2026, 10, 30, 16, 35, tzinfo=NEW_YORK)
    with pytest.raises(ExecutionRefused, match="not the next scheduled trading session"):
        await trader.execute_persisted_signal(now_ny=t_same_day, signal_file=sig_file)


@pytest.mark.asyncio
async def test_execute_guard_refuses_outside_window(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, sig_file = synthetic_oct2026_trader
    t_mon_morning = dt.datetime(2026, 11, 2, 9, 31, tzinfo=NEW_YORK)
    with pytest.raises(ExecutionRefused, match="outside the execution window"):
        await trader.execute_persisted_signal(now_ny=t_mon_morning, signal_file=sig_file)


@pytest.mark.asyncio
async def test_execute_guard_succeeds_in_window(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, sig_file = synthetic_oct2026_trader
    t_mon_window = dt.datetime(2026, 11, 2, 15, 50, tzinfo=NEW_YORK)
    orders = await trader.execute_persisted_signal(now_ny=t_mon_window, signal_file=sig_file)
    assert len(orders) > 0


@pytest.mark.asyncio
async def test_execute_guard_refuses_when_killswitch_latched(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, sig_file = synthetic_oct2026_trader
    t_mon_window = dt.datetime(2026, 11, 2, 15, 50, tzinfo=NEW_YORK)
    # Trip the killswitch with a large loss
    trader.killswitch.record_realized_r(Decimal("-5"), at=t_mon_window.astimezone(dt.UTC))
    assert trader.killswitch.is_tripped

    with pytest.raises(ExecutionRefused, match="Kill-switch is latched"):
        await trader.execute_persisted_signal(now_ny=t_mon_window, signal_file=sig_file)


@pytest.mark.asyncio
async def test_execute_guard_refuses_invalid_universe_symbols(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, sig_file = synthetic_oct2026_trader
    t_mon_window = dt.datetime(2026, 11, 2, 15, 50, tzinfo=NEW_YORK)
    # Tamper with signal file to inject an unapproved stock
    data = json.loads(sig_file.read_text(encoding="utf-8"))
    data["target_weights"]["AAPL"] = "0.3333"
    sig_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid symbols not in strategy universe"):
        await trader.execute_persisted_signal(now_ny=t_mon_window, signal_file=sig_file)


@pytest.mark.asyncio
async def test_execute_guard_refuses_excess_weight_sum(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, sig_file = synthetic_oct2026_trader
    t_mon_window = dt.datetime(2026, 11, 2, 15, 50, tzinfo=NEW_YORK)
    # Tamper with signal file to inject leverage (weight sum > 1.0001)
    data = json.loads(sig_file.read_text(encoding="utf-8"))
    for k in data["target_weights"]:
        data["target_weights"][k] = "0.75"
    sig_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match=r"exceeds 1\.0001 threshold"):
        await trader.execute_persisted_signal(now_ny=t_mon_window, signal_file=sig_file)


def test_signal_guard_refuses_non_month_end(
    synthetic_oct2026_trader: tuple[LiveTrader, Path],
) -> None:
    trader, _ = synthetic_oct2026_trader
    # Wednesday 2026-10-28 16:30 ET is not month-end
    t_wed = dt.datetime(2026, 10, 28, 16, 30, tzinfo=NEW_YORK)
    with pytest.raises(SignalRefused, match="not the final trading day"):
        trader.generate_and_persist_signal(now_ny=t_wed, allow_non_month_end=False)
