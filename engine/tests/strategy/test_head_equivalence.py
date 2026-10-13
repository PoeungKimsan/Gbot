"""Property test comparing HEAD strategy vs current working copy across 200 seeds and 12 month-ends,
plus round-trip refresh (04:00/05:00Z timestamps) -> CSVs -> reader -> strategy.
"""

from __future__ import annotations

import datetime as dt
import random
import subprocess
import sys
import types
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from engine.feed.alpaca_feed import AlpacaFeed
from engine.feed.refresh_data import refresh_etf_universe_data
from engine.market.nyse_calendar import get_nyse_trading_days, is_last_trading_day_of_month
from engine.strategy.etf_momentum import DEFAULT_UNIVERSE, VAR117Strategy

NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def head_strategy_cls() -> type:
    """Dynamically load the HEAD version of VAR117Strategy via git show."""
    git_res = subprocess.run(
        ["git", "show", "HEAD:engine/src/engine/strategy/etf_momentum.py"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    mod = types.ModuleType("head_etf_momentum")
    sys.modules["head_etf_momentum"] = mod
    exec(git_res.stdout, mod.__dict__)  # noqa: S102
    return mod.VAR117Strategy


def _generate_random_price_series(
    seed: int,
    tickers: tuple[str, ...] = DEFAULT_UNIVERSE,
    length: int = 280,
    end_date: dt.date = dt.date(2025, 12, 31),
) -> dict[str, list[tuple[dt.date, Decimal]]]:
    rng = random.Random(seed)  # noqa: S311
    start_date = end_date - dt.timedelta(days=length * 2)
    trading_days = get_nyse_trading_days(start_date, end_date)[-length:]

    prices: dict[str, list[tuple[dt.date, Decimal]]] = {}
    for t in tickers:
        base = Decimal(str(rng.randint(50, 400)))
        step = Decimal(str(round(rng.uniform(-0.5, 0.8), 2)))
        noise_scale = round(rng.uniform(0.01, 0.5), 2)
        series: list[tuple[dt.date, Decimal]] = []
        curr = base
        for d in trading_days:
            noise = Decimal(str(round(rng.uniform(-noise_scale, noise_scale), 2)))
            curr = max(Decimal("1.00"), curr + step + noise)
            series.append((d, curr.quantize(Decimal("0.01"))))
        prices[t] = series
    return prices


def test_property_head_vs_current_200_seeds(head_strategy_cls: type) -> None:
    """Compare HEAD vs current on 200 random seeds for both cash_filter on and off."""
    as_of = dt.date(2025, 12, 31)

    for seed in range(200):
        prices = _generate_random_price_series(seed=seed, end_date=as_of)
        head_prices = {t: [p for _, p in s] for t, s in prices.items()}

        for cf in (True, False):
            strat_head = head_strategy_cls(top_k=3, cash_filter=cf)
            strat_curr = VAR117Strategy(top_k=3, cash_filter=cf)

            sig_head = strat_head.compute_allocation(head_prices, as_of_date=as_of)
            sig_curr = strat_curr.compute_allocation(prices, as_of_date=as_of)

            # 1. Top assets match exactly
            assert sig_head.top_assets == sig_curr.top_assets, (
                f"Seed {seed} (cf={cf}) top assets mismatch"
            )

            # 2. Weights match exactly
            assert sig_head.target_weights == sig_curr.target_weights, (
                f"Seed {seed} (cf={cf}) weights mismatch"
            )

            # 3. Cash weight matches exactly
            assert sig_head.cash_weight == sig_curr.cash_weight, (
                f"Seed {seed} (cf={cf}) cash mismatch"
            )

            # 4. Scores match
            head_dict = sig_head.to_dict()
            curr_dict = sig_curr.to_dict()
            assert head_dict["scores"] == curr_dict["scores"], (
                f"Seed {seed} (cf={cf}) score details mismatch"
            )


def test_head_vs_current_12_month_ends(head_strategy_cls: type) -> None:
    """Compare HEAD vs current strategy across 12 real month-ends using seed CSVs."""
    feed = AlpacaFeed(data_dir=Path("engine/data/etf_cache"))
    all_prices = feed.load_historical_prices(min_bars=253)

    # Extract all 12 month-end trading days in 2025 (where lookback >= 253 trading days)
    ref_series = all_prices["SPY"]
    all_dates = [d for d, _ in ref_series]
    month_ends = [d for d in all_dates if is_last_trading_day_of_month(d) and d.year == 2025]
    assert len(month_ends) == 12

    for me in month_ends:
        # Slice prices up to me
        curr_prices = {
            t: [(d, p) for d, p in series if d <= me]
            for t, series in all_prices.items()
        }
        head_prices = {
            t: [p for d, p in series if d <= me]
            for t, series in all_prices.items()
        }

        for cf in (True, False):
            strat_head = head_strategy_cls(top_k=3, cash_filter=cf)
            strat_curr = VAR117Strategy(top_k=3, cash_filter=cf)

            sig_head = strat_head.compute_allocation(head_prices, as_of_date=me)
            sig_curr = strat_curr.compute_allocation(curr_prices, as_of_date=me)

            assert sig_head.top_assets == sig_curr.top_assets, (
                f"{me} (cf={cf}) top assets mismatch"
            )
            assert sig_head.target_weights == sig_curr.target_weights, (
                f"{me} (cf={cf}) weights mismatch"
            )
            assert sig_head.cash_weight == sig_curr.cash_weight, (
                f"{me} (cf={cf}) cash mismatch"
            )
            assert sig_head.to_dict()["scores"] == sig_curr.to_dict()["scores"], (
                f"{me} (cf={cf}) scores mismatch"
            )


@pytest.mark.asyncio
async def test_round_trip_refresh_alpaca_timestamps_to_strategy(tmp_path: Path) -> None:
    """Round trip: mocked Alpaca refresh with UTC timestamps -> CSVs -> feed -> strategy."""
    end_date = dt.date(2025, 12, 31)
    trading_days = get_nyse_trading_days(dt.date(2024, 1, 1), end_date)
    assert len(trading_days) >= 253

    # Handler simulating Alpaca API returning UTC morning timestamps (04:00Z or 05:00Z)
    def handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        sym = "SPY"
        for candidate in DEFAULT_UNIVERSE:
            if f"/{candidate}/" in url_str:
                sym = candidate
                break

        base_val = Decimal("100") + Decimal(DEFAULT_UNIVERSE.index(sym) * 10)
        bars_json = []
        for i, d in enumerate(trading_days):
            # Alternate 04:00Z and 05:00Z depending on daylight saving
            hour_str = "04:00:00Z" if d.month in (4, 5, 6, 7, 8, 9, 10) else "05:00:00Z"
            price = base_val + Decimal(i) * Decimal("0.20")
            bars_json.append({
                "t": f"{d.isoformat()}T{hour_str}",
                "c": float(price),
            })
        return httpx.Response(200, json={"bars": bars_json})

    transport = httpx.MockTransport(handler)
    real_client_cls = httpx.AsyncClient

    target_dir = tmp_path / "refreshed_data"
    with patch(
        "httpx.AsyncClient",
        side_effect=lambda *args, **kwargs: real_client_cls(transport=transport),
    ):
        manifest = await refresh_etf_universe_data(
            feed="sip",
            expected_last_date=end_date.isoformat(),
            target_dir=target_dir,
            api_key="mock_key",
            api_secret="mock_secret",
            universe=DEFAULT_UNIVERSE,
            min_bars=253,
        )

    assert manifest is not None
    assert (target_dir / "manifest.json").is_file()

    # Ingest with AlpacaFeed
    feed = AlpacaFeed(data_dir=target_dir, feed="sip")
    prices = feed.load_historical_prices(min_bars=253, expected_as_of_date=end_date)
    assert len(prices) == 12

    # Compute allocation with VAR117Strategy
    strategy = VAR117Strategy()
    signal = strategy.compute_allocation(prices, as_of_date=end_date)
    assert signal.as_of_date == end_date
    assert len(signal.top_assets) == 3
    assert len(signal.scores) == 12
    assert sum(signal.target_weights.values()) + signal.cash_weight == Decimal("1.0000")
