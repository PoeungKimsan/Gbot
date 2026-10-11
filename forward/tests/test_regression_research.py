"""Regression test: running engine over 2002-2025 reproduces research trade lists exactly.

Tests both:
1. Canonical London-session fixed calendar (T2=374, T3=373) under data_fxfixed/ and output_fxfixed/.
2. Legacy unfixed calendar (T2=374, T3=377) under data/ and output/.
"""

import csv
import datetime as dt
from pathlib import Path

import pytest
from research.trend.indicators import load_daily_bars
from research.trend.models import TrendTrade
from research.trend.strategies import run_t2_sma_cross, run_t3_tsmom

DATA_DIR = Path("research/trend/data")
RESEARCH_OUTPUT_DIR = Path("research/trend/output")
DATA_FXFIXED_DIR = Path("research/trend/data_fxfixed")
OUTPUT_FXFIXED_DIR = Path("research/trend/output_fxfixed")

SYMBOLS = [
    "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD", "USDJPY",
    "AUDUSD", "USDCAD", "USDCHF", "US500", "GER40", "Brent", "NDX",
]
END_DATE = dt.date(2025, 12, 31)


def _load_csv(folder: Path, fname: str) -> list[dict]:
    with open(folder / fname, encoding="utf-8") as f:
        return list(csv.DictReader(f))


# --- Canonical Fixed Calendar Tests (Target: T2=374, T3=373) ---

def test_t2_sma_cross_canonical_fixed_reproduces_exactly():
    """Verify that running T2 on data_fxfixed reproduces output_fxfixed bit-for-bit."""
    expected_trades = _load_csv(OUTPUT_FXFIXED_DIR, "T2_SMA_CROSS_trades.csv")
    assert len(expected_trades) == 374

    actual_trades: list[TrendTrade] = []
    for sym in SYMBOLS:
        bars = load_daily_bars(DATA_FXFIXED_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        sym_trades = run_t2_sma_cross(sym, bars, start_date=None, end_date=END_DATE)
        actual_trades.extend(sym_trades)

    assert len(actual_trades) == len(expected_trades)
    for i, (act, exp) in enumerate(zip(actual_trades, expected_trades)):
        assert act.trade_id == exp["trade_id"]
        assert act.symbol == exp["symbol"]
        assert act.direction == exp["direction"]
        assert act.entry_date.isoformat() == exp["entry_date"]
        assert act.exit_date.isoformat() == exp["exit_date"]
        assert pytest.approx(act.entry_price, rel=1e-5) == float(exp["entry_price"])
        assert pytest.approx(act.exit_price, rel=1e-5) == float(exp["exit_price"])
        assert pytest.approx(act.gross_r, rel=1e-5) == float(exp["gross_r"])
        assert pytest.approx(act.net_r_fin0, rel=1e-5) == float(exp["net_r_fin0"])
        assert pytest.approx(act.net_r, rel=1e-5) == float(exp["net_r"])


def test_t3_tsmom_canonical_fixed_reproduces_exactly():
    """Verify that running T3 on data_fxfixed reproduces output_fxfixed bit-for-bit (T3=373)."""
    expected_trades = _load_csv(OUTPUT_FXFIXED_DIR, "T3_TSMOM_trades.csv")
    assert len(expected_trades) == 373

    actual_trades: list[TrendTrade] = []
    for sym in SYMBOLS:
        bars = load_daily_bars(DATA_FXFIXED_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        sym_trades = run_t3_tsmom(sym, bars, start_date=None, end_date=END_DATE)
        actual_trades.extend(sym_trades)

    assert len(actual_trades) == len(expected_trades)
    for i, (act, exp) in enumerate(zip(actual_trades, expected_trades)):
        assert act.trade_id == exp["trade_id"]
        assert act.symbol == exp["symbol"]
        assert act.direction == exp["direction"]
        assert act.entry_date.isoformat() == exp["entry_date"]
        assert act.exit_date.isoformat() == exp["exit_date"]
        assert pytest.approx(act.entry_price, rel=1e-5) == float(exp["entry_price"])
        assert pytest.approx(act.exit_price, rel=1e-5) == float(exp["exit_price"])
        assert pytest.approx(act.gross_r, rel=1e-5) == float(exp["gross_r"])
        assert pytest.approx(act.net_r_fin0, rel=1e-5) == float(exp["net_r_fin0"])
        assert pytest.approx(act.net_r, rel=1e-5) == float(exp["net_r"])


# --- Legacy Unfixed Calendar Tests (Baseline: T2=374, T3=377) ---

def test_t2_sma_cross_legacy_unfixed_reproduces_exactly():
    """Verify legacy reproduction on unfixed data."""
    expected_trades = _load_csv(RESEARCH_OUTPUT_DIR, "T2_SMA_CROSS_trades.csv")
    assert len(expected_trades) == 374

    actual_trades: list[TrendTrade] = []
    for sym in SYMBOLS:
        bars = load_daily_bars(DATA_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        sym_trades = run_t2_sma_cross(sym, bars, start_date=None, end_date=END_DATE)
        actual_trades.extend(sym_trades)

    assert len(actual_trades) == len(expected_trades)


def test_t3_tsmom_legacy_unfixed_reproduces_exactly():
    """Verify legacy reproduction on unfixed data (T3=377)."""
    expected_trades = _load_csv(RESEARCH_OUTPUT_DIR, "T3_TSMOM_trades.csv")
    assert len(expected_trades) == 377

    actual_trades: list[TrendTrade] = []
    for sym in SYMBOLS:
        bars = load_daily_bars(DATA_DIR / f"{sym}_daily.csv", hard_stop_date=END_DATE)
        sym_trades = run_t3_tsmom(sym, bars, start_date=None, end_date=END_DATE)
        actual_trades.extend(sym_trades)

    assert len(actual_trades) == len(expected_trades)
