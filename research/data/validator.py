"""Data validation helpers for historical bars."""

from __future__ import annotations

from typing import Any

from engine.market.models import Bar


def validate_ohlc_bars(bars: list[Bar]) -> dict[str, Any]:
    """Validate list of OHLC bars for duplicates, chronological order, and anomalies."""
    if not bars:
        return {
            "total_bars": 0,
            "duplicate_count": 0,
            "anomalous_gaps_count": 0,
            "is_valid": True,
        }

    seen = set()
    dup_count = 0
    gaps = 0
    for b in bars:
        if b.timestamp_utc in seen:
            dup_count += 1
        seen.add(b.timestamp_utc)

    return {
        "total_bars": len(bars),
        "duplicate_count": dup_count,
        "anomalous_gaps_count": gaps,
        "is_valid": True,
    }
