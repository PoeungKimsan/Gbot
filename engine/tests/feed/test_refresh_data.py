"""Unit tests for ETF historical price cache refresh and manifest generation."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from engine.feed.refresh_data import (
    atomic_write_csv,
    get_manifest,
    refresh_etf_universe_data,
)
from engine.market.nyse_calendar import get_nyse_trading_days


def test_atomic_write_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "test.csv"
    rows = [
        {"Date": "2024-01-02", "Close": "100.50", "Adj Close": "100.50"},
        {"Date": "2024-01-03", "Close": "101.20", "Adj Close": "101.20"},
    ]
    file_hash = atomic_write_csv(csv_path, rows)

    assert csv_path.is_file()
    assert len(file_hash) == 64
    content = csv_path.read_text(encoding="utf-8")
    assert "Date,Close,Adj Close" in content
    assert "2024-01-02,100.50,100.50" in content


@pytest.mark.asyncio
async def test_refresh_etf_universe_data_without_target_or_env_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ETF_DATA_DIR", raising=False)
    with pytest.raises(ValueError, match=r"ETF_DATA_DIR environment variable is unset"):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date="2024-12-31",
            target_dir=None,
            api_key="key",
            api_secret="sec",
        )


@pytest.mark.asyncio
async def test_refresh_refuses_the_seed_dir(tmp_path: Path) -> None:
    seed_like_path = tmp_path / "engine" / "data" / "etf_cache"
    seed_like_path.mkdir(parents=True)
    with pytest.raises(ValueError, match=r"Refused to refresh directly into engine/data/etf_cache"):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date="2024-12-31",
            target_dir=seed_like_path,
            api_key="key",
            api_secret="sec",
        )


@pytest.mark.asyncio
async def test_refresh_rejects_invalid_or_missing_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    # 1. Missing feed raises
    with pytest.raises(ValueError, match=r"feed is required"):
        await refresh_etf_universe_data(
            feed=None,
            expected_last_date="2024-12-31",
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
        )

    # 2. Invalid feed raises
    with pytest.raises(ValueError, match=r"Invalid feed 'bats'"):
        await refresh_etf_universe_data(
            feed="bats",
            expected_last_date="2024-12-31",
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
        )


@pytest.mark.asyncio
async def test_fewer_than_253_bars_raises(tmp_path: Path) -> None:
    short_rows = [{"Date": "2024-01-02", "Close": "500.00", "Adj Close": "500.00"}]
    with (
        patch(
            "engine.feed.refresh_data.fetch_bars_from_alpaca",
            new=AsyncMock(return_value=short_rows),
        ),
        pytest.raises(ValueError, match=r"Insufficient bars fetched for SPY: got 1, need >= 253"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date="2024-01-02",
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY",),
            min_bars=253,
        )


@pytest.mark.asyncio
async def test_refresh_rejects_duplicate_dates(tmp_path: Path) -> None:
    trading_days = get_nyse_trading_days(dt.date(2024, 1, 2), dt.date(2025, 1, 10))
    rows = [{"Date": d.isoformat(), "Close": "100.00", "Adj Close": "100.00"} for d in trading_days]
    # Insert duplicate date
    rows.insert(5, {"Date": trading_days[4].isoformat(), "Close": "100.00", "Adj Close": "100.00"})

    with (
        patch("engine.feed.refresh_data.fetch_bars_from_alpaca", new=AsyncMock(return_value=rows)),
        pytest.raises(ValueError, match=r"Duplicate date .* found in SPY bars"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=trading_days[-1].isoformat(),
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY",),
            min_bars=253,
        )


@pytest.mark.asyncio
async def test_refresh_rejects_unsorted_dates(tmp_path: Path) -> None:
    trading_days = get_nyse_trading_days(dt.date(2024, 1, 2), dt.date(2025, 1, 10))
    rows = [{"Date": d.isoformat(), "Close": "100.00", "Adj Close": "100.00"} for d in trading_days]
    # Swap two dates
    rows[10], rows[11] = rows[11], rows[10]

    with (
        patch("engine.feed.refresh_data.fetch_bars_from_alpaca", new=AsyncMock(return_value=rows)),
        pytest.raises(ValueError, match=r"Unsorted date sequence"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=trading_days[-1].isoformat(),
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY",),
            min_bars=253,
        )


@pytest.mark.asyncio
async def test_refresh_rejects_blank_or_nan_or_nonpositive_prices(tmp_path: Path) -> None:
    trading_days = get_nyse_trading_days(dt.date(2024, 1, 2), dt.date(2025, 1, 10))
    rows_nan = [
        {"Date": d.isoformat(), "Close": "100.00", "Adj Close": "100.00"}
        for d in trading_days
    ]
    rows_nan[2]["Adj Close"] = "NaN"

    with (
        patch(
            "engine.feed.refresh_data.fetch_bars_from_alpaca",
            new=AsyncMock(return_value=rows_nan),
        ),
        pytest.raises(ValueError, match=r"Blank or NaN price"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=trading_days[-1].isoformat(),
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY",),
            min_bars=253,
        )

    rows_zero = [
        {"Date": d.isoformat(), "Close": "100.00", "Adj Close": "100.00"}
        for d in trading_days
    ]
    rows_zero[2]["Adj Close"] = "0.00"

    with (
        patch(
            "engine.feed.refresh_data.fetch_bars_from_alpaca",
            new=AsyncMock(return_value=rows_zero),
        ),
        pytest.raises(ValueError, match=r"Non-positive price"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=trading_days[-1].isoformat(),
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY",),
            min_bars=253,
        )


@pytest.mark.asyncio
async def test_refresh_rejects_gaps_against_nyse_calendar(tmp_path: Path) -> None:
    trading_days = get_nyse_trading_days(dt.date(2024, 1, 2), dt.date(2025, 1, 10))
    rows = [{"Date": d.isoformat(), "Close": "100.00", "Adj Close": "100.00"} for d in trading_days]
    # Drop a trading day in the middle
    rows.pop(10)

    with (
        patch("engine.feed.refresh_data.fetch_bars_from_alpaca", new=AsyncMock(return_value=rows)),
        pytest.raises(ValueError, match=r"Gap against NYSE calendar detected"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=trading_days[-1].isoformat(),
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY",),
            min_bars=253,
        )


@pytest.mark.asyncio
async def test_refresh_rejects_unexpected_last_date(tmp_path: Path) -> None:
    trading_days = get_nyse_trading_days(dt.date(2024, 1, 2), dt.date(2025, 1, 10))
    rows = [{"Date": d.isoformat(), "Close": "100.00", "Adj Close": "100.00"} for d in trading_days]

    with (
        patch("engine.feed.refresh_data.fetch_bars_from_alpaca", new=AsyncMock(return_value=rows)),
        pytest.raises(ValueError, match=r"does not match expected_last_date 2025-01-15"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date="2025-01-15",
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY",),
            min_bars=253,
        )


@pytest.mark.asyncio
async def test_failed_fetch_leaves_old_files_intact_atomicity(tmp_path: Path) -> None:
    old_csv = tmp_path / "SPY_daily.csv"
    old_csv.write_text("Date,Close,Adj Close\n2024-01-02,400,400\n", encoding="utf-8")
    old_manifest = tmp_path / "manifest.json"
    old_manifest.write_text('{"old": "manifest"}', encoding="utf-8")

    trading_days = get_nyse_trading_days(dt.date(2024, 1, 2), dt.date(2025, 1, 10))
    valid_bars = [
        {"Date": d.isoformat(), "Close": "400.00", "Adj Close": "400.00"}
        for d in trading_days
    ]

    async def mock_fetch(symbol: str, **kwargs: object) -> list[dict[str, str]]:
        if symbol == "SPY":
            return valid_bars
        return []  # QQQ fails (< 253 bars)

    with (
        patch("engine.feed.refresh_data.fetch_bars_from_alpaca", side_effect=mock_fetch),
        pytest.raises(ValueError, match=r"Insufficient bars fetched for QQQ"),
    ):
        await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=trading_days[-1].isoformat(),
            target_dir=tmp_path,
            api_key="key",
            api_secret="sec",
            universe=("SPY", "QQQ"),
            min_bars=253,
        )

    assert old_csv.read_text(encoding="utf-8") == "Date,Close,Adj Close\n2024-01-02,400,400\n"
    assert old_manifest.read_text(encoding="utf-8") == '{"old": "manifest"}'
    assert not (tmp_path / ".refresh_staging").exists()


@pytest.mark.asyncio
async def test_manifest_written_at_fetch_time_with_mocked_http(tmp_path: Path) -> None:
    """Validate realistic fetch-time manifest creation with distinct series and dates per ticker."""
    trading_days = get_nyse_trading_days(dt.date(2024, 1, 2), dt.date(2025, 1, 10))
    expected_last_date = trading_days[-1].isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert "feed=sip" in str(request.url)
        assert "adjustment=all" in str(request.url)
        # Determine symbol from URL
        sym = "SPY" if "/SPY/" in str(request.url) else "QQQ"
        base_price = 450.0 if sym == "SPY" else 380.0
        step = 0.5 if sym == "SPY" else 0.75

        bars_json = [
            {"t": f"{d.isoformat()}T00:00:00Z", "c": base_price + i * step}
            for i, d in enumerate(trading_days)
        ]
        return httpx.Response(200, json={"bars": bars_json})

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.AsyncClient

    with patch(
        "httpx.AsyncClient",
        side_effect=lambda *args, **kwargs: real_client_cls(transport=transport),
    ):
        manifest = await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=expected_last_date,
            target_dir=tmp_path,
            api_key="test_key",
            api_secret="test_secret",
            universe=("SPY", "QQQ"),
            min_bars=253,
        )

    manifest_dict = manifest.to_dict()
    assert manifest_dict["source"] == "Alpaca Market Data v2"
    assert manifest_dict["adjustment"] == "all"
    assert manifest_dict["requested_feed"] == "sip"
    assert "fetched_at" in manifest_dict
    assert "SPY" in manifest_dict["symbols"]
    assert "QQQ" in manifest_dict["symbols"]

    spy_meta = manifest_dict["symbols"]["SPY"]
    qqq_meta = manifest_dict["symbols"]["QQQ"]

    # Distinct SHA-256 because series are different
    assert spy_meta["sha256"] != qqq_meta["sha256"]
    assert len(spy_meta["sha256"]) == 64
    assert len(qqq_meta["sha256"]) == 64

    # Date ranges and row counts are consistent with 258 trading days
    assert spy_meta["first_date"] == "2024-01-02"
    assert spy_meta["last_date"] == expected_last_date
    assert spy_meta["rows"] == len(trading_days)
    assert qqq_meta["first_date"] == "2024-01-02"
    assert qqq_meta["last_date"] == expected_last_date
    assert qqq_meta["rows"] == len(trading_days)

    # Verify manifest on disk
    disk_manifest = get_manifest(tmp_path)
    assert disk_manifest is not None
    assert disk_manifest["symbols"]["SPY"]["sha256"] == spy_meta["sha256"]
    assert disk_manifest["symbols"]["QQQ"]["sha256"] == qqq_meta["sha256"]
