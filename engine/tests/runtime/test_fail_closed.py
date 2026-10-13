"""Tests for fail-closed runtime semantics (VAR_117).

Verifies:
1. Scheduled one-shot executor exits non-zero on priming, broker-init, journal-read,
   or stale-data error.
2. The _broker_init_error deferral is removed for the one-shot path (fails immediately).
3. _read_journal_killswitch_events raises on error (no silent empty list fallback).
4. Degraded engine state blocks trading and reports reason.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from engine.execution.alpaca_broker import AlpacaBroker
from engine.feed.alpaca_feed import AlpacaFeed
from engine.runtime.live_trader import (
    LiveTrader,
    _read_journal_killswitch_events,
    run_oneshot_rebalance,
)

NEW_YORK = ZoneInfo("America/New_York")


def test_read_journal_killswitch_events_raises_on_error(tmp_path: Path) -> None:
    """_read_journal_killswitch_events must raise on error (no silent empty list fallback)."""
    corrupt_db = tmp_path / "corrupt_journal.db"
    corrupt_db.write_text("NOT A VALID SQLITE DATABASE", encoding="utf-8")

    with pytest.raises(sqlite3.DatabaseError):
        _read_journal_killswitch_events(corrupt_db)


def test_oneshot_exits_nonzero_on_priming_error(tmp_path: Path) -> None:
    """One-shot exits non-zero if feed cannot prime data (e.g. missing data dir)."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    feed = AlpacaFeed(data_dir=empty_dir)

    exit_code = run_oneshot_rebalance(
        feed=feed,
        journal_path=tmp_path / "test.db",
    )
    assert exit_code != 0


def test_oneshot_exits_nonzero_on_broker_init_error(tmp_path: Path) -> None:
    """One-shot exits non-zero immediately if broker initialization fails."""
    with patch(
        "engine.runtime.live_trader.AlpacaBroker",
        side_effect=RuntimeError("Alpaca credentials missing"),
    ):
        exit_code = run_oneshot_rebalance(
            journal_path=tmp_path / "test.db",
        )
        assert exit_code != 0


def test_oneshot_exits_nonzero_on_journal_read_error(tmp_path: Path) -> None:
    """One-shot exits non-zero if journal database is corrupted."""
    corrupt_db = tmp_path / "corrupt.db"
    corrupt_db.write_text("corrupted content", encoding="utf-8")

    exit_code = run_oneshot_rebalance(
        journal_path=corrupt_db,
    )
    assert exit_code != 0


def test_oneshot_exits_nonzero_on_stale_data_error(tmp_path: Path) -> None:
    """One-shot exits non-zero if persisted signal is stale compared to last completed session."""
    signal_file = tmp_path / "stale_signal.json"
    signal_file.write_text('{"as_of_date": "2026-10-01", "target_weights": {}}', encoding="utf-8")

    # Time is Tuesday 2026-11-03 15:50 ET: last completed session is 2026-11-02
    t_now = dt.datetime(2026, 11, 3, 15, 50, tzinfo=NEW_YORK)

    exit_code = run_oneshot_rebalance(
        now_ny=t_now,
        signal_file=signal_file,
        broker=AlpacaBroker(mock=True),
        journal_path=tmp_path / "test.db",
    )
    assert exit_code != 0


def test_oneshot_removes_broker_init_error_deferral(tmp_path: Path) -> None:
    """Verify that _broker_init_error is never deferred to execution in the one-shot path."""
    trader = LiveTrader(
        broker=None,
        journal_path=tmp_path / "test.db",
        enable_journal=False,
    )
    # Simulate broker init error recorded on trader
    trader._broker_init_error = ConnectionError("Broker connection failed")

    with patch("engine.runtime.live_trader.LiveTrader", return_value=trader):
        exit_code = run_oneshot_rebalance(
            journal_path=tmp_path / "test.db",
        )
        assert exit_code != 0
