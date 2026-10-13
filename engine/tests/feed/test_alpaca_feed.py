"""Unit tests for AlpacaFeed and historical ETF cache loading."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from engine.feed.alpaca_feed import (
    DEFAULT_ETF_UNIVERSE,
    AlpacaFeed,
    AlpacaFeedError,
    Quote,
)

SEED_DATA_DIR = Path("engine/data/etf_cache")


def _write_single_asset_dataset(
    dir_path: Path,
    symbol: str,
    rows: list[str],
    manifest_last_date: str = "2025-12-31",
) -> None:
    csv_file = dir_path / f"{symbol}_daily.csv"
    csv_content = "Date,Close,Adj Close\n" + "\n".join(rows) + "\n"
    csv_file.write_text(csv_content, encoding="utf-8")
    sha256 = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()

    manifest_file = dir_path / "manifest.json"
    manifest_data = {
        "symbols": {
            symbol: {
                "sha256": sha256,
                "first_date": rows[0].split(",")[0] if rows else "2025-01-01",
                "last_date": manifest_last_date,
                "rows": len(rows),
            }
        }
    }
    manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")


def test_alpaca_feed_loads_historical_prices() -> None:
    feed = AlpacaFeed(data_dir=SEED_DATA_DIR)
    prices = feed.load_historical_prices(min_bars=253)

    assert len(prices) == 12
    for ticker in DEFAULT_ETF_UNIVERSE:
        assert ticker in prices
        series = prices[ticker]
        assert len(series) >= 253
        # Ensure each bar is (date, price) with positive Decimal price
        assert isinstance(series[-1][0], dt.date)
        assert isinstance(series[-1][1], Decimal)
        assert series[-1][1] > Decimal("0")


@pytest.mark.asyncio
async def test_alpaca_feed_fetch_latest_quotes() -> None:
    feed = AlpacaFeed(data_dir=SEED_DATA_DIR)
    # Manually populate mock quotes
    feed.set_mock_quotes({t: Decimal("500.00") for t in DEFAULT_ETF_UNIVERSE})
    quotes = await feed.fetch_latest_quotes()

    assert len(quotes) == 12
    for ticker in DEFAULT_ETF_UNIVERSE:
        assert ticker in quotes
        quote = quotes[ticker]
        assert isinstance(quote, Quote)
        assert quote.price == Decimal("500.00")
        assert quote.feed in ("sip", "iex")


def test_reader_raises_when_etf_data_dir_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ETF_DATA_DIR", raising=False)
    with pytest.raises(AlpacaFeedError, match=r"ETF_DATA_DIR environment variable is unset"):
        AlpacaFeed(data_dir=None)


def test_missing_manifest_raises(tmp_path: Path) -> None:
    csv_file = tmp_path / "SPY_daily.csv"
    csv_file.write_text("Date,Close,Adj Close\n2025-01-02,500,500\n", encoding="utf-8")

    feed = AlpacaFeed(universe=("SPY",), data_dir=tmp_path)
    with pytest.raises(AlpacaFeedError, match=r"Missing manifest\.json"):
        feed.load_historical_prices(min_bars=1)


def test_sha256_mismatch_raises(tmp_path: Path) -> None:
    csv_file = tmp_path / "SPY_daily.csv"
    csv_file.write_text("Date,Close,Adj Close\n2025-01-02,500,500\n", encoding="utf-8")

    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(
        '{"symbols": {"SPY": {"sha256": "bad_corrupted_hash"}}}',
        encoding="utf-8",
    )

    feed = AlpacaFeed(universe=("SPY",), data_dir=tmp_path)
    with pytest.raises(AlpacaFeedError, match=r"SHA-256 hash mismatch for SPY"):
        feed.load_historical_prices(min_bars=1)


def test_feed_requires_adj_close(tmp_path: Path) -> None:
    csv_file = tmp_path / "SPY_daily.csv"
    csv_content = "Date,Close\n2025-01-02,500.00\n"
    csv_file.write_text(csv_content, encoding="utf-8")
    sha256 = hashlib.sha256(csv_content.encode("utf-8")).hexdigest()

    manifest_file = tmp_path / "manifest.json"
    manifest_file.write_text(
        json.dumps({"symbols": {"SPY": {"sha256": sha256}}}),
        encoding="utf-8",
    )

    feed = AlpacaFeed(universe=("SPY",), data_dir=tmp_path)
    with pytest.raises(AlpacaFeedError, match=r"Missing required 'Adj Close' column"):
        feed.load_historical_prices(min_bars=1)


def test_missing_adj_close(tmp_path: Path) -> None:
    test_feed_requires_adj_close(tmp_path)


def test_feed_rejects_blank_or_nan_or_nonpositive_rows(tmp_path: Path) -> None:
    # 1. Blank price
    _write_single_asset_dataset(tmp_path, "SPY", ["2025-01-02,500.00,"])
    feed = AlpacaFeed(universe=("SPY",), data_dir=tmp_path)
    with pytest.raises(AlpacaFeedError, match=r"Blank or NaN price"):
        feed.load_historical_prices(min_bars=1)

    # 2. NaN price
    _write_single_asset_dataset(tmp_path, "SPY", ["2025-01-02,500.00,NaN"])
    with pytest.raises(AlpacaFeedError, match=r"Blank or NaN price"):
        feed.load_historical_prices(min_bars=1)

    # 3. Non-positive price
    _write_single_asset_dataset(tmp_path, "SPY", ["2025-01-02,0.00,0.00"])
    with pytest.raises(AlpacaFeedError, match=r"Non-positive price"):
        feed.load_historical_prices(min_bars=1)


def test_blank_row(tmp_path: Path) -> None:
    test_feed_rejects_blank_or_nan_or_nonpositive_rows(tmp_path)


def test_feed_requires_sorted_unique_dates(tmp_path: Path) -> None:
    # Duplicate dates
    _write_single_asset_dataset(
        tmp_path,
        "SPY",
        ["2025-01-02,500.00,500.00", "2025-01-02,501.00,501.00"],
    )
    feed = AlpacaFeed(universe=("SPY",), data_dir=tmp_path)
    with pytest.raises(AlpacaFeedError, match=r"Duplicate date"):
        feed.load_historical_prices(min_bars=1)

    # Unsorted dates
    _write_single_asset_dataset(
        tmp_path,
        "SPY",
        ["2025-01-03,500.00,500.00", "2025-01-02,501.00,501.00"],
    )
    with pytest.raises(AlpacaFeedError, match=r"Unsorted date sequence"):
        feed.load_historical_prices(min_bars=1)


def test_feed_raises_on_daily_return_over_25_pct(tmp_path: Path) -> None:
    # +50% move on adjacent days
    _write_single_asset_dataset(
        tmp_path,
        "SPY",
        ["2025-01-02,100.00,100.00", "2025-01-03,150.00,150.00"],
    )
    feed = AlpacaFeed(universe=("SPY",), data_dir=tmp_path)
    with pytest.raises(AlpacaFeedError, match=r"exceeds 25% threshold for SPY"):
        feed.load_historical_prices(min_bars=1)


@pytest.mark.asyncio
async def test_stale_quote() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    feed = AlpacaFeed(
        universe=("SPY",),
        data_dir=SEED_DATA_DIR,
        api_key="test_key",
        api_secret="test_sec",
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "bars": {
            "SPY": {"c": 500.0, "t": "2025-01-01T15:00:00Z"}
        }
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        now_ny = dt.datetime(2025, 1, 2, 10, 0, 0, tzinfo=dt.UTC)
        with pytest.raises(AlpacaFeedError, match=r"Stale quote for SPY"):
            await feed.fetch_latest_quotes(current_time=now_ny)


def test_stale_manifest(tmp_path: Path) -> None:
    _write_single_asset_dataset(
        tmp_path,
        "SPY",
        ["2025-01-02,500.00,500.00"],
        manifest_last_date="2025-01-02",
    )
    feed = AlpacaFeed(universe=("SPY",), data_dir=tmp_path)
    with pytest.raises(AlpacaFeedError, match=r"Dataset is stale: manifest last_date"):
        feed.load_historical_prices(min_bars=1, expected_as_of_date="2025-12-31")
