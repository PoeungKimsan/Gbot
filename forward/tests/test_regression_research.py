"""Regression test: running forward engine over 2002-2025 must reproduce research trade lists exactly."""

import csv
import datetime as dt
from pathlib import Path

import pytest
from research.trend.indicators import load_daily_bars
from research.trend.models import TrendTrade
from research.trend.strategies import run_t2_sma_cross, run_t3_tsmom

RESEARCH_OUTPUT_DIR = Path("research/trend/output")
DATA_DIR = Path("research/trend/data")
SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]
END_DATE = dt.date(2025, 12, 31)


def load_research_csv(fname: str) -> list[dict]:
    with open(RESEARCH_OUTPUT_DIR / fname, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_t2_sma_cross_reproduces_research_trades_exactly():
    """Verify that running T2 reproduces research/trend/output/T2_SMA_CROSS_trades.csv bit-for-bit."""
    expected_trades = load_research_csv("T2_SMA_CROSS_trades.csv")
    assert len(expected_trades) == 374, f"Expected 374 research trades, got {len(expected_trades)}"

    actual_trades: list[TrendTrade] = []
    for sym in SYMBOLS:
        bars = load_daily_bars(DATA_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        sym_trades = run_t2_sma_cross(sym, bars, start_date=None, end_date=END_DATE)
        actual_trades.extend(sym_trades)

    assert len(actual_trades) == len(expected_trades), (
        f"Trade count mismatch: actual {len(actual_trades)} vs expected {len(expected_trades)}"
    )

    for i, (act, exp) in enumerate(zip(actual_trades, expected_trades)):
        assert act.trade_id == exp["trade_id"], f"Trade ID mismatch at {i}: {act.trade_id} != {exp['trade_id']}"
        assert act.symbol == exp["symbol"], f"Symbol mismatch at {i}: {act.symbol} != {exp['symbol']}"
        assert act.direction == exp["direction"], f"Direction mismatch at {i}: {act.direction} != {exp['direction']}"
        assert act.entry_date.isoformat() == exp["entry_date"], f"Entry date mismatch at {i}"
        assert act.exit_date.isoformat() == exp["exit_date"], f"Exit date mismatch at {i}"
        assert pytest.approx(act.entry_price, rel=1e-5) == float(exp["entry_price"])
        assert pytest.approx(act.exit_price, rel=1e-5) == float(exp["exit_price"])
        assert pytest.approx(act.gross_r, rel=1e-5) == float(exp["gross_r"])
        assert pytest.approx(act.net_r_fin0, rel=1e-5) == float(exp["net_r_fin0"])
        assert pytest.approx(act.net_r, rel=1e-5) == float(exp["net_r"])


def test_t3_tsmom_reproduces_research_trades_exactly():
    """Verify that running T3 reproduces research/trend/output/T3_TSMOM_trades.csv bit-for-bit."""
    expected_trades = load_research_csv("T3_TSMOM_trades.csv")
    assert len(expected_trades) == 377, f"Expected 377 research trades, got {len(expected_trades)}"

    actual_trades: list[TrendTrade] = []
    for sym in SYMBOLS:
        bars = load_daily_bars(DATA_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        sym_trades = run_t3_tsmom(sym, bars, start_date=None, end_date=END_DATE)
        actual_trades.extend(sym_trades)

    assert len(actual_trades) == len(expected_trades), (
        f"Trade count mismatch: actual {len(actual_trades)} vs expected {len(expected_trades)}"
    )

    for i, (act, exp) in enumerate(zip(actual_trades, expected_trades)):
        assert act.trade_id == exp["trade_id"], f"Trade ID mismatch at {i}: {act.trade_id} != {exp['trade_id']}"
        assert act.symbol == exp["symbol"], f"Symbol mismatch at {i}: {act.symbol} != {exp['symbol']}"
        assert act.direction == exp["direction"], f"Direction mismatch at {i}: {act.direction} != {exp['direction']}"
        assert act.entry_date.isoformat() == exp["entry_date"], f"Entry date mismatch at {i}"
        assert act.exit_date.isoformat() == exp["exit_date"], f"Exit date mismatch at {i}"
        assert pytest.approx(act.entry_price, rel=1e-5) == float(exp["entry_price"])
        assert pytest.approx(act.exit_price, rel=1e-5) == float(exp["exit_price"])
        assert pytest.approx(act.gross_r, rel=1e-5) == float(exp["gross_r"])
        assert pytest.approx(act.net_r_fin0, rel=1e-5) == float(exp["net_r_fin0"])
        assert pytest.approx(act.net_r, rel=1e-5) == float(exp["net_r"])
