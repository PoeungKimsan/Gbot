"""Unit tests for forward/forward_log.py."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from forward.forward_log import (
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
