"""VAR_117 Multi-Asset ETF Momentum Rotation Strategy with Cash Filter.

Implements the pre-registered winning model:
1. Universe: 12 US ETFs (SPY, QQQ, IWM, EFA, EEM, IEF, TLT, SHY, LQD, VNQ, GLD, DBC).
2. Trailing 9-month (189 trading days) momentum ranking.
3. Top K=3 selection.
4. Cash Filter (positive momentum hurdle R_9M > 0; non-positive allocated to cash).
5. 12-month (252 trading days) inverse-volatility weighting among passing assets.
6. Strictly zero-float: all mathematical operations are performed with exact Decimals.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)

__all__ = [
    "ETF_METADATA",
    "PortfolioAllocationSignal",
    "TickerScore",
    "VAR117Strategy",
]

ETF_METADATA: dict[str, dict[str, str]] = {
    "SPY": {"name": "SPDR S&P 500 ETF", "category": "US Large Cap Equity"},
    "QQQ": {"name": "Invesco QQQ Trust", "category": "US Tech / Growth Equity"},
    "IWM": {"name": "iShares Russell 2000 ETF", "category": "US Small Cap Equity"},
    "EFA": {"name": "iShares MSCI EAFE ETF", "category": "Developed Int'l Equity"},
    "EEM": {"name": "iShares MSCI Emerging Markets", "category": "Emerging Markets Equity"},
    "IEF": {"name": "iShares 7-10 Year Treasury", "category": "US Intermediate Treasury"},
    "TLT": {"name": "iShares 20+ Year Treasury", "category": "US Long-Term Treasury"},
    "SHY": {"name": "iShares 1-3 Year Treasury", "category": "US Short-Term Treasury"},
    "LQD": {"name": "iShares iBoxx $ Inv Grade Corp", "category": "Corporate Bonds"},
    "VNQ": {"name": "Vanguard Real Estate ETF", "category": "Real Estate / REITs"},
    "GLD": {"name": "SPDR Gold Trust", "category": "Precious Metals / Gold"},
    "DBC": {"name": "Invesco DB Commodity Index", "category": "Broad Commodities"},
}

DEFAULT_UNIVERSE: tuple[str, ...] = tuple(ETF_METADATA.keys())


@dataclass(frozen=True, slots=True)
class TickerScore:
    ticker: str
    name: str
    category: str
    close_price: Decimal
    mom_9m_pct: Decimal
    vol_12m_pct: Decimal
    rank: int | None
    cash_filter_status: str  # "PASS", "FAIL", "N/A"
    target_weight: Decimal


@dataclass(frozen=True, slots=True)
class PortfolioAllocationSignal:
    as_of_date: dt.date
    target_weights: dict[str, Decimal]  # Ticker -> Decimal weight
    cash_weight: Decimal
    top_assets: list[str]
    scores: list[TickerScore]
    signal_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "as_of_date": str(self.as_of_date),
            "target_weights": {k: str(v) for k, v in self.target_weights.items()},
            "cash_weight": str(self.cash_weight),
            "top_assets": list(self.top_assets),
            "signal_hash": self.signal_hash,
            "scores": [
                {
                    "ticker": s.ticker,
                    "name": s.name,
                    "category": s.category,
                    "close_price": str(s.close_price),
                    "mom_9m_pct": str(s.mom_9m_pct),
                    "vol_12m_pct": str(s.vol_12m_pct),
                    "rank": s.rank,
                    "cash_filter_status": s.cash_filter_status,
                    "target_weight": str(s.target_weight),
                }
                for s in self.scores
            ],
        }


class VAR117Strategy:
    """Pre-registered VAR_117 Multi-Asset ETF Momentum Rotation Strategy."""

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
        """Compute target weights and 12-ETF ranking table from daily price history.

        Parameters
        ----------
        price_history : dict[str, list[Decimal]]
            Mapping of ticker to sorted historical daily close prices (oldest to newest).
            Must have at least `min_required_days` prices for each universe asset.
        as_of_date : dt.date | None
            Effective date of signal. Defaults to today's UTC date.
        """
        eval_date = as_of_date or dt.datetime.now(dt.UTC).date()
        min_bars = self.min_required_days

        # Validate sufficient history for all universe tickers
        for ticker in self.universe:
            prices = price_history.get(ticker)
            if prices is None or len(prices) < min_bars:
                bars_count = len(prices) if prices else 0
                raise ValueError(
                    f"Insufficient history for {ticker}: got {bars_count} bars, "
                    f"need at least {min_bars}"
                )

        # 1. Trailing 9-month momentum and 12-month volatility calculation
        moms: dict[str, Decimal] = {}
        vols: dict[str, Decimal] = {}
        latest_prices: dict[str, Decimal] = {}

        annualization_factor = Decimal("252").sqrt()

        for ticker in self.universe:
            prices = price_history[ticker]
            latest_p = prices[-1]
            latest_prices[ticker] = latest_p

            # 9-month momentum: (P_latest - P_{latest - lookback}) / P_{latest - lookback}
            past_p = prices[-1 - self.momentum_lookback_days]
            mom = Decimal("0") if past_p <= Decimal("0") else (latest_p - past_p) / past_p
            moms[ticker] = mom

            # 12-month daily returns and sample standard deviation
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
                annual_vol = Decimal("0.15")  # Safe fallback if insufficient variance

            # Guard against zero volatility
            if annual_vol <= Decimal("0"):
                annual_vol = Decimal("0.0001")
            vols[ticker] = annual_vol

        # 2. Rank assets by 9M momentum descending (deterministic tie-break alphabetically)
        sorted_tickers = sorted(
            self.universe,
            key=lambda t: (-moms[t], t),
        )

        top_assets = sorted_tickers[: self.top_k]

        # 3. Inverse-volatility weighting among top K
        inv_vols: dict[str, Decimal] = {}
        for t in top_assets:
            inv_vols[t] = Decimal("1") / vols[t]

        sum_inv = sum(inv_vols.values())

        # 4. Cash Filter Gating
        weights: dict[str, Decimal] = {}
        for t in top_assets:
            raw_w = (inv_vols[t] / sum_inv) if sum_inv > Decimal("0") else Decimal("0")
            if self.cash_filter:
                # Must have positive trailing return (> 0)
                if moms[t] > Decimal("0"):
                    weights[t] = raw_w.quantize(Decimal("0.0001"))
                else:
                    weights[t] = Decimal("0.0000")
            else:
                weights[t] = raw_w.quantize(Decimal("0.0001"))

        # Zero out non-top assets
        for t in self.universe:
            if t not in weights:
                weights[t] = Decimal("0.0000")

        # Ensure total invested weight never exceeds 1.0000 due to rounding
        invested_sum = sum(weights.values())
        if invested_sum > Decimal("1.0000"):
            excess = invested_sum - Decimal("1.0000")
            max_t = max(weights.keys(), key=lambda k: weights[k])
            weights[max_t] = max(Decimal("0.0000"), weights[max_t] - excess)
            invested_sum = sum(weights.values())

        # Cash gets whatever is unallocated
        cash_weight = max(Decimal("0.0000"), Decimal("1.0000") - invested_sum)

        # 5. Build detailed score records for radar & reporting
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

        # Canonical hash for immutability & audit logging
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
