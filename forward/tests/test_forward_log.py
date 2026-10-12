"""Unit tests for forward/forward_log.py."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from forward.forward_log import (
    append_forward_log_records,
    compute_indicators,
    evaluate_t2_sma_cross,
)


def test_zero_engine_imports() -> None:
    """Invariant: forward_log.py must not import engine packages."""
    source_path = Path("forward/forward_log.py")
    assert source_path.is_file()
    source_text = source_path.read_text(encoding="utf-8")
    assert "import engine" not in source_text
    assert "from engine" not in source_text


def test_compute_indicators_synthetic() -> None:
    """Test indicator calculation on synthetic daily bars."""
    bars = []
    base_date = dt.date(2025, 1, 1)
    for i in range(300):
        d = base_date + dt.timedelta(days=i)
        p = 100.0 + i * 0.5
        bars.append({
            "date": d,
            "open": p,
            "high": p + 1.0,
            "low": p - 1.0,
            "close": p + 0.2,
            "volume": 1000,
            "is_forming": False,
        })

    enriched = compute_indicators(bars)
    assert len(enriched) == 300
    assert enriched[-1]["atr20"] > 0.0
    assert enriched[-1]["sma50"] > 0.0
    assert enriched[-1]["sma200"] > 0.0
    assert enriched[-1]["ret252"] > 0.0


def test_start_forward_date_filtering() -> None:
    """Test that trades prior to 2026-10-12 are excluded from forward_trades."""
    # Create synthetic crossover before 2026-10-12
    bars = []
    base_date = dt.date(2025, 1, 1)
    for i in range(250):
        d = base_date + dt.timedelta(days=i)
        # Flat then sudden rise
        p = 100.0 if i < 210 else 150.0
        bars.append({
            "date": d,
            "open": p,
            "high": p + 1.0,
            "low": p - 1.0,
            "close": p,
            "volume": 1000,
            "is_forming": False,
        })

    enriched = compute_indicators(bars)
    # Forward cutoff is 2026-10-12
    trades = evaluate_t2_sma_cross("EURUSD", enriched, start_forward_date=dt.date(2026, 10, 12))
    # All synthetic bars were in 2025, so forward trades must be 0
    assert len(trades) == 0


def test_running_twice_does_not_duplicate_rows(tmp_path: Path) -> None:
    """Requirement 2: Running twice for the same date must not duplicate rows."""
    test_csv = tmp_path / "forward_log_test.csv"
    sample_records = [
        {
            "date": "2026-10-12",
            "market": "EURUSD",
            "rule": "T2_SMA_CROSS",
            "signal": "BUY",
            "entry": 1.0500,
            "stop": 1.0400,
            "exit": None,
            "R": None,
            "R_gross": None,
            "R_net": None,
        },
        {
            "date": "2026-10-12",
            "market": "USDJPY",
            "rule": "T3_252D_HIGH",
            "signal": "BUY",
            "entry": 150.25,
            "stop": 148.50,
            "exit": None,
            "R": None,
            "R_gross": None,
            "R_net": None,
        },
    ]

    # First run: writes header and 2 records
    first_added = append_forward_log_records(sample_records, csv_path=test_csv)
    assert first_added == 2
    assert test_csv.is_file()

    with open(test_csv, encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2
        assert reader[0]["market"] == "EURUSD"
        assert reader[1]["market"] == "USDJPY"

    # Second run for the same date & records: must not duplicate rows
    second_added = append_forward_log_records(sample_records, csv_path=test_csv)
    assert second_added == 0

    with open(test_csv, encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 2, f"Expected 2 rows, found {len(reader)} (duplicates created)"

    # Third run with one duplicate and one new record
    mixed_records = [
        sample_records[0],  # Duplicate
        {
            "date": "2026-10-12",
            "market": "GBPUSD",
            "rule": "T2_SMA_CROSS",
            "signal": "SELL",
            "entry": 1.2500,
            "stop": 1.2600,
            "exit": None,
            "R": None,
            "R_gross": None,
            "R_net": None,
        },
    ]
    third_added = append_forward_log_records(mixed_records, csv_path=test_csv)
    assert third_added == 1

    with open(test_csv, encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
        assert len(reader) == 3

