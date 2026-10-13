"""Unit tests for engine.runtime.oneshot CLI."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from engine.execution.alpaca_broker import AlpacaBroker, AlpacaBrokerError
from engine.runtime.oneshot import run_execute_phase, run_signal_phase

NEW_YORK = ZoneInfo("America/New_York")
T_FRI_CLOSE = dt.datetime(2026, 10, 30, 16, 30, tzinfo=NEW_YORK)
T_MON_WINDOW = dt.datetime(2026, 11, 2, 15, 50, tzinfo=NEW_YORK)


@pytest.fixture
def synthetic_data(tmp_path: Path, build_synthetic_dataset: Any) -> tuple[Path, Path]:
    cache_dir = tmp_path / "etf_cache"
    build_synthetic_dataset(cache_dir, end_date=dt.date(2026, 10, 30), num_bars=260)
    sig_file = tmp_path / "latest_signal.json"
    return cache_dir, sig_file


def test_oneshot_signal_and_execute_success(
    synthetic_data: tuple[Path, Path], tmp_path: Path
) -> None:
    cache_dir, sig_file = synthetic_data
    journal_path = tmp_path / "journal.db"

    # 1. Signal phase (no refresh needed since data is pre-built)
    code_sig = run_signal_phase(
        signal_file=sig_file,
        data_dir=cache_dir,
        refresh=False,
        now_ny=T_FRI_CLOSE,
        journal_path=journal_path,
    )
    assert code_sig == 0
    assert sig_file.is_file()

    # 2. Execute phase
    code_exec = run_execute_phase(
        signal_file=sig_file,
        data_dir=cache_dir,
        now_ny=T_MON_WINDOW,
        journal_path=journal_path,
        broker=AlpacaBroker(mock=True),
    )
    assert code_exec == 0


def test_oneshot_signal_refused_on_non_month_end(
    synthetic_data: tuple[Path, Path], tmp_path: Path
) -> None:
    cache_dir, sig_file = synthetic_data
    t_wed = dt.datetime(2026, 10, 28, 16, 30, tzinfo=NEW_YORK)
    code = run_signal_phase(
        signal_file=sig_file,
        data_dir=cache_dir,
        refresh=False,
        now_ny=t_wed,
        allow_non_month_end=False,
        journal_path=tmp_path / "journal.db",
    )
    assert code == 2


def test_oneshot_execute_refused_on_saturday(
    synthetic_data: tuple[Path, Path], tmp_path: Path
) -> None:
    cache_dir, sig_file = synthetic_data
    journal_path = tmp_path / "journal.db"

    run_signal_phase(
        signal_file=sig_file,
        data_dir=cache_dir,
        refresh=False,
        now_ny=T_FRI_CLOSE,
        journal_path=journal_path,
    )

    t_sat = dt.datetime(2026, 10, 31, 12, 0, tzinfo=NEW_YORK)
    code = run_execute_phase(
        signal_file=sig_file,
        data_dir=cache_dir,
        now_ny=t_sat,
        journal_path=journal_path,
        broker=AlpacaBroker(mock=True),
    )
    assert code == 2


def test_oneshot_execute_returns_3_on_broker_failure(
    synthetic_data: tuple[Path, Path], tmp_path: Path
) -> None:
    cache_dir, sig_file = synthetic_data
    journal_path = tmp_path / "journal.db"

    run_signal_phase(
        signal_file=sig_file,
        data_dir=cache_dir,
        refresh=False,
        now_ny=T_FRI_CLOSE,
        journal_path=journal_path,
    )

    mock_broker = AlpacaBroker(mock=True)
    with patch.object(
        mock_broker,
        "rebalance_portfolio",
        side_effect=AlpacaBrokerError("Sell failed for SPY"),
    ):
        code = run_execute_phase(
            signal_file=sig_file,
            data_dir=cache_dir,
            now_ny=T_MON_WINDOW,
            journal_path=journal_path,
            broker=mock_broker,
        )
        assert code == 3
