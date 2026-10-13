"""Property test comparing the pre-refactor baseline VAR_117 strategy against current working copy.

Verifies bit-for-bit equivalence across 200 random seeds and 12 real 2025 month-ends,
plus round-trip refresh (04:00/05:00Z timestamps) -> CSVs -> reader -> strategy.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from engine.feed.alpaca_feed import AlpacaFeed
from engine.feed.refresh_data import refresh_etf_universe_data
from engine.market.nyse_calendar import get_nyse_trading_days, is_last_trading_day_of_month
from engine.strategy.etf_momentum import (
    DEFAULT_UNIVERSE,
    ETF_METADATA,
    PortfolioAllocationSignal,
    TickerScore,
    VAR117Strategy,
)

NEW_YORK = ZoneInfo("America/New_York")


class BaselineVAR117Strategy:
    """Immutable pre-refactor reference implementation of VAR_117 (from commit 77e9f0f).

    Operates purely on undated close prices, representing the exact baseline grid-search logic.
    """

    def __init__(
        self,
        universe: tuple[str, ...] = DEFAULT_UNIVERSE,
        momentum_lookback_days: int = 189,
        volatility_lookback_days: int = 252,
        top_k: int = 3,
        cash_filter: bool = True,
    ) -> None:
        self.universe = list(universe)
        self.momentum_lookback_days = momentum_lookback_days
        self.volatility_lookback_days = volatility_lookback_days
        self.top_k = top_k
        self.cash_filter = cash_filter

    @property
    def min_required_days(self) -> int:
        return max(self.momentum_lookback_days, self.volatility_lookback_days) + 1

    def compute_allocation(
        self,
        price_history: dict[str, list[Decimal]],
        as_of_date: dt.date | None = None,
    ) -> PortfolioAllocationSignal:
        eval_date = as_of_date or dt.datetime.now(dt.UTC).date()
        min_bars = self.min_required_days

        for ticker in self.universe:
            prices = price_history.get(ticker)
            if prices is None or len(prices) < min_bars:
                bars_count = len(prices) if prices else 0
                raise ValueError(
                    f"Insufficient history for {ticker}: got {bars_count} bars, "
                    f"need at least {min_bars}"
                )

        moms: dict[str, Decimal] = {}
        vols: dict[str, Decimal] = {}
        latest_prices: dict[str, Decimal] = {}
        annualization_factor = Decimal("252").sqrt()

        for ticker in self.universe:
            prices = price_history[ticker]
            latest_p = prices[-1]
            latest_prices[ticker] = latest_p

            past_p = prices[-1 - self.momentum_lookback_days]
            mom = Decimal("0") if past_p <= Decimal("0") else (latest_p - past_p) / past_p
            moms[ticker] = mom

            recent_prices = prices[-1 - self.volatility_lookback_days :]
            daily_rets: list[Decimal] = []
            for i in range(1, len(recent_prices)):
                prev = recent_prices[i - 1]
                curr = recent_prices[i]
                if prev > Decimal("0"):
                    daily_rets.append((curr - prev) / prev)
                else:
                    daily_rets.append(Decimal("0"))

            n_rets = len(daily_rets)
            if n_rets > 1:
                mean_ret = sum(daily_rets) / Decimal(n_rets)
                sum_sq_diff = sum((r - mean_ret) ** 2 for r in daily_rets)
                variance = sum_sq_diff / Decimal(n_rets - 1)
                daily_std = variance.sqrt()
                annual_vol = daily_std * annualization_factor
            else:
                annual_vol = Decimal("0.15")

            if annual_vol <= Decimal("0"):
                annual_vol = Decimal("0.0001")
            vols[ticker] = annual_vol

        sorted_tickers = sorted(
            self.universe,
            key=lambda t: (-moms[t], t),
        )
        top_assets = sorted_tickers[: self.top_k]

        inv_vols: dict[str, Decimal] = {}
        for t in top_assets:
            inv_vols[t] = Decimal("1") / vols[t]

        sum_inv = sum(inv_vols.values())

        weights: dict[str, Decimal] = {}
        for t in top_assets:
            raw_w = (inv_vols[t] / sum_inv) if sum_inv > Decimal("0") else Decimal("0")
            if self.cash_filter:
                if moms[t] > Decimal("0"):
                    weights[t] = raw_w.quantize(Decimal("0.0001"))
                else:
                    weights[t] = Decimal("0.0000")
            else:
                weights[t] = raw_w.quantize(Decimal("0.0001"))

        for t in self.universe:
            if t not in weights:
                weights[t] = Decimal("0.0000")

        invested_sum = sum(weights.values())
        if invested_sum > Decimal("1.0000"):
            excess = invested_sum - Decimal("1.0000")
            max_t = max(weights.keys(), key=lambda k: weights[k])
            weights[max_t] = max(Decimal("0.0000"), weights[max_t] - excess)
            invested_sum = sum(weights.values())

        cash_weight = max(Decimal("0.0000"), Decimal("1.0000") - invested_sum)

        scores: list[TickerScore] = []
        for t in sorted_tickers:
            is_top = t in top_assets
            rank = (sorted_tickers.index(t) + 1) if is_top else None
            w = weights.get(t, Decimal("0.0000"))
            mom_pct = (moms[t] * Decimal("100")).quantize(Decimal("0.01"))
            vol_pct = (vols[t] * Decimal("100")).quantize(Decimal("0.01"))

            cf_status = ("PASS" if moms[t] > Decimal("0") else "FAIL") if is_top else "N/A"
            meta = ETF_METADATA.get(t, {"name": t, "category": "ETF"})
            scores.append(
                TickerScore(
                    ticker=t,
                    name=meta["name"],
                    category=meta["category"],
                    close_price=latest_prices[t].quantize(Decimal("0.01")),
                    mom_9m_pct=mom_pct,
                    vol_12m_pct=vol_pct,
                    rank=rank,
                    cash_filter_status=cf_status,
                    target_weight=w,
                )
            )

        canonical_payload = json.dumps(
            {
                "date": str(eval_date),
                "weights": {k: str(weights[k]) for k in sorted(weights.keys())},
                "cash": str(cash_weight),
            },
            sort_keys=True,
        )
        sig_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

        return PortfolioAllocationSignal(
            as_of_date=eval_date,
            target_weights=weights,
            cash_weight=cash_weight,
            top_assets=top_assets,
            scores=scores,
            signal_hash=sig_hash,
        )


@pytest.fixture(scope="module")
def head_strategy_cls() -> type:
    """Provide the reference pre-refactor baseline VAR_117 strategy class."""
    return BaselineVAR117Strategy


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
    """Compare baseline reference vs current on 200 random seeds for both cash_filter on/off."""
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
    """Compare baseline vs current strategy across 12 real month-ends using seed CSVs."""
    feed = AlpacaFeed(data_dir=Path("engine/data/etf_cache"))
    all_prices = feed.load_historical_prices(min_bars=253)

    # Extract all 12 month-end trading days in 2025 (where lookback >= 253 trading days)
    ref_series = all_prices["SPY"]
    all_dates = [d for d, _ in ref_series]
    month_ends = [d for d in all_dates if is_last_trading_day_of_month(d) and d.year == 2025]
    assert len(month_ends) == 12

    for me in month_ends:
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

    feed = AlpacaFeed(data_dir=target_dir, feed="sip")
    prices = feed.load_historical_prices(min_bars=253, expected_as_of_date=end_date)
    assert len(prices) == 12

    strategy = VAR117Strategy()
    signal = strategy.compute_allocation(prices, as_of_date=end_date)
    assert signal.as_of_date == end_date
    assert len(signal.top_assets) == 3
    assert len(signal.scores) == 12
    assert sum(signal.target_weights.values()) + signal.cash_weight == Decimal("1.0000")
