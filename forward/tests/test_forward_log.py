"""Unit tests for forward/forward_log.py."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from unittest.mock import MagicMock

import pytest

from forward.forward_log import (
    append_forward_log_records,
    compute_indicators,
    evaluate_t2_sma_cross,
    run_forward_log,
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


def test_mt5_init_failure_exits_nonzero_and_logs(tmp_path: Path) -> None:
    """Requirement 3: If MT5 init fails, append a FAILED line to forward_run.log and exit non-zero."""
    test_csv = tmp_path / "forward_log.csv"
    test_run_log = tmp_path / "forward_run.log"

    mock_mt5 = MagicMock()
    mock_mt5.initialize.return_value = False
    mock_mt5.last_error.return_value = (-10004, "Terminal connection refused")

    with pytest.raises(SystemExit) as exc_info:
        run_forward_log(log_csv_path=test_csv, run_log_path=test_run_log, mt5_client=mock_mt5)

    assert exc_info.value.code != 0
    assert exc_info.value.code == 1

    assert test_run_log.is_file()
    log_content = test_run_log.read_text(encoding="utf-8")
    assert "status=FAILED" in log_content
    assert "MT5 failed to initialize" in log_content


def test_no_new_bars_exits_nonzero_and_logs(tmp_path: Path) -> None:
    """Requirement 3: If no new bars arrive, append a NO_NEW_BARS line to forward_run.log and exit non-zero."""
    test_csv = tmp_path / "forward_log.csv"
    test_run_log = tmp_path / "forward_run.log"

    mock_mt5 = MagicMock()
    mock_mt5.initialize.return_value = True
    # copy_rates_from_pos returns empty rates (0 new bars)
    mock_mt5.copy_rates_from_pos.return_value = []

    with pytest.raises(SystemExit) as exc_info:
        run_forward_log(log_csv_path=test_csv, run_log_path=test_run_log, mt5_client=mock_mt5)

    assert exc_info.value.code != 0
    assert exc_info.value.code == 2

    assert test_run_log.is_file()
    log_content = test_run_log.read_text(encoding="utf-8")
    assert "status=NO_NEW_BARS" in log_content


def test_open_run1_close_run2_not_dropped(tmp_path: Path) -> None:
    """Requirement 4: Confirm trade open on run 1 and close on run 2 is not dropped by duplicate check."""
    test_csv = tmp_path / "forward_log.csv"

    # Run 1: Trade is opened on 2026-10-12
    open_record = {
        "date": "2026-10-12",
        "market": "EURUSD",
        "rule": "T2_SMA_CROSS",
        "signal": "BUY",
        "entry": "1.05000",
        "stop": "1.04000",
        "exit": "OPEN",
        "R": "N/A",
        "R_gross": "N/A",
        "R_net": "N/A",
    }
    added_run1 = append_forward_log_records([open_record], csv_path=test_csv)
    assert added_run1 == 1

    with open(test_csv, encoding="utf-8") as f:
        rows_1 = list(csv.DictReader(f))
        assert len(rows_1) == 1
        assert rows_1[0]["exit"] == "OPEN"
        assert rows_1[0]["date"] == "2026-10-12"

    # Run 2: Trade closes on 2026-10-15 (both open record and closed record returned by evaluator)
    exit_record = {
        "date": "2026-10-15",
        "market": "EURUSD",
        "rule": "T2_SMA_CROSS",
        "signal": "BUY",
        "entry": "1.05000",
        "stop": "1.04000",
        "exit": "1.06000",
        "R": "+1.0000",
        "R_gross": "+1.0500",
        "R_net": "+1.0000",
    }
    records_run2 = [open_record, exit_record]
    added_run2 = append_forward_log_records(records_run2, csv_path=test_csv)

    # Exactly 1 row added on run 2 (the exit row; open_record deduped)
    assert added_run2 == 1

    # Confirm the exit row is present in the CSV and not dropped
    with open(test_csv, encoding="utf-8") as f:
        rows_2 = list(csv.DictReader(f))
        assert len(rows_2) == 2, f"Expected 2 rows, found {len(rows_2)}"
        assert rows_2[0]["exit"] == "OPEN"
        assert rows_2[0]["date"] == "2026-10-12"
        assert rows_2[1]["exit"] == "1.06000"
        assert rows_2[1]["date"] == "2026-10-15"


