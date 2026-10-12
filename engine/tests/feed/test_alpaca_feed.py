"""Unit tests for AlpacaFeed and historical ETF cache loading."""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.feed.alpaca_feed import DEFAULT_ETF_UNIVERSE, AlpacaFeed


def test_alpaca_feed_loads_historical_prices() -> None:
    feed = AlpacaFeed()
    prices = feed.load_historical_prices(min_bars=253)

    assert len(prices) == 12
    for ticker in DEFAULT_ETF_UNIVERSE:
        assert ticker in prices
        series = prices[ticker]
        assert len(series) >= 253
        # Ensure all prices are Decimals and positive
        assert isinstance(series[-1], Decimal)
        assert series[-1] > Decimal("0")


@pytest.mark.asyncio
async def test_alpaca_feed_fetch_latest_quotes() -> None:
    feed = AlpacaFeed()
    feed.load_historical_prices(min_bars=253)
    quotes = await feed.fetch_latest_quotes()

    assert len(quotes) == 12
    for ticker in DEFAULT_ETF_UNIVERSE:
        assert ticker in quotes
        assert isinstance(quotes[ticker], Decimal)
        assert quotes[ticker] > Decimal("0")
