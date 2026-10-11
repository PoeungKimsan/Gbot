"""Tests for MT5Feed metric extraction and data feed accuracy.

Verifies:
1. Account summary accurately maps balance, equity, margin, free margin, profit, and currency.
2. Quote prices and timestamps are accurately converted to UTC and exact Decimals.
3. Historical bars are properly constructed with exact 3-decimal OHLC and spread handling.
4. Symbol resolution handles broker-specific suffix variants correctly.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from engine.feed.mt5_client import MT5Feed, MT5FeedError
from engine.market.models import Timeframe


@pytest.fixture(autouse=True)
def _mock_mt5_init() -> Iterator[None]:
    """Isolate tests from desktop MT5 terminal."""
    mock_sym = MagicMock()
    mock_sym.visible = True
    with (
        patch("MetaTrader5.initialize", return_value=True),
        patch("MetaTrader5.symbol_info", return_value=mock_sym),
    ):
        yield


def test_get_account_summary_metrics_accuracy() -> None:
    feed = MT5Feed(requested_symbol="XAUUSDm")

    mock_account = MagicMock()
    mock_account.login = 12345678
    mock_account.name = "Gold Trader"
    mock_account.server = "Exness-Real19"
    mock_account.balance = 5420.50
    mock_account.equity = 5485.75
    mock_account.margin = 150.25
    mock_account.margin_free = 5335.50
    mock_account.profit = 65.25
    mock_account.leverage = 500
    mock_account.currency = "USD"

    with patch("MetaTrader5.account_info", return_value=mock_account):
        summary = feed.get_account_summary()

        assert summary["login"] == 12345678
        assert summary["name"] == "Gold Trader"
        assert summary["server"] == "Exness-Real19"
        assert summary["balance"] == "5420.5"
        assert summary["equity"] == "5485.75"
        assert summary["margin"] == "150.25"
        assert summary["free_margin"] == "5335.5"
        assert summary["profit"] == "65.25"
        assert summary["leverage"] == 500
        assert summary["currency"] == "USD"


def test_get_account_summary_raises_on_none() -> None:
    feed = MT5Feed(requested_symbol="XAUUSDm")

    with (
        patch("MetaTrader5.account_info", return_value=None),
        patch("MetaTrader5.last_error", return_value=(-1, "Common error")),
        pytest.raises(MT5FeedError, match="Failed to get account info"),
    ):
        feed.get_account_summary()


def test_get_latest_quote_metrics_accuracy() -> None:
    feed = MT5Feed(requested_symbol="XAUUSDm")

    mock_symbol_info = MagicMock()
    mock_symbol_info.visible = True

    mock_tick = MagicMock()
    mock_tick.time = 1710000000  # 2024-03-09T16:00:00Z
    mock_tick.bid = 2705.456
    mock_tick.ask = 2705.756

    with (
        patch("MetaTrader5.symbol_info", return_value=mock_symbol_info),
        patch("MetaTrader5.symbol_info_tick", return_value=mock_tick),
    ):
        quote = feed.get_latest_quote()

        assert quote is not None
        assert quote.bid == Decimal("2705.456")
        assert quote.ask == Decimal("2705.756")
        assert quote.timestamp_utc == dt.datetime(2024, 3, 9, 16, 0, 0, tzinfo=dt.UTC)


def test_get_latest_quote_rejects_invalid_ticks() -> None:
    feed = MT5Feed(requested_symbol="XAUUSDm")

    mock_symbol_info = MagicMock()
    mock_symbol_info.visible = True

    mock_zero_tick = MagicMock()
    mock_zero_tick.time = 1710000000
    mock_zero_tick.bid = 0.0
    mock_zero_tick.ask = 2700.0

    with (
        patch("MetaTrader5.symbol_info", return_value=mock_symbol_info),
        patch("MetaTrader5.symbol_info_tick", return_value=mock_zero_tick),
    ):
        assert feed.get_latest_quote() is None


def test_fetch_historical_bars_metrics_accuracy() -> None:
    feed = MT5Feed(requested_symbol="XAUUSDm")

    mock_symbol_info = MagicMock()
    mock_symbol_info.visible = True

    # 1 candle: 2700 open, 2710 high, 2695 low, 2705 close
    # spread = 30 points (0.30 usd, half = 0.15)
    mock_rate = {
        "time": 1710003600,
        "open": 2700.000,
        "high": 2710.000,
        "low": 2695.000,
        "close": 2705.000,
        "tick_volume": 450,
        "spread": 30,
        "real_volume": 0,
    }

    with (
        patch("MetaTrader5.symbol_info", return_value=mock_symbol_info),
        patch("MetaTrader5.copy_rates_from_pos", return_value=[mock_rate]),
    ):
        bars = feed.fetch_historical_bars(timeframe=Timeframe.M5, count=1)

        assert len(bars) == 1
        b = bars[0]
        assert b.timeframe == "M5"
        assert b.timestamp_utc == dt.datetime(2024, 3, 9, 17, 0, 0, tzinfo=dt.UTC)
        assert b.volume == 450
        assert b.complete is True

        # Half spread for 30 points = 0.150
        assert b.bid_ohlc.open == Decimal("2699.850")
        assert b.bid_ohlc.high == Decimal("2709.850")
        assert b.bid_ohlc.low == Decimal("2694.850")
        assert b.bid_ohlc.close == Decimal("2704.850")

        assert b.ask_ohlc.open == Decimal("2700.150")
        assert b.ask_ohlc.high == Decimal("2710.150")
        assert b.ask_ohlc.low == Decimal("2695.150")
        assert b.ask_ohlc.close == Decimal("2705.150")

        # Mid OHLC matches raw rate prices exactly
        assert b.mid_ohlc.open == Decimal("2700.000")
        assert b.mid_ohlc.high == Decimal("2710.000")
        assert b.mid_ohlc.low == Decimal("2695.000")
        assert b.mid_ohlc.close == Decimal("2705.000")


def test_symbol_resolution_variants() -> None:
    feed = MT5Feed(requested_symbol="XAUUSD")

    # Simulate broker that only has XAUUSDm (e.g. Exness Standard)
    def fake_symbol_info(name: str) -> MagicMock | None:
        if name == "XAUUSDm":
            m = MagicMock()
            m.visible = False
            return m
        return None

    with (
        patch("MetaTrader5.symbol_info", side_effect=fake_symbol_info),
        patch("MetaTrader5.symbol_select") as mock_select,
    ):
        resolved = feed.resolve_symbol("XAUUSD")
        assert resolved == "XAUUSDm"
        mock_select.assert_called_with("XAUUSDm", True)
