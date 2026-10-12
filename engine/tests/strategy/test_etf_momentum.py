"""Unit tests for VAR117Strategy (Multi-Asset ETF Momentum Rotation)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from engine.strategy.etf_momentum import (
    DEFAULT_UNIVERSE,
    TickerScore,
    VAR117Strategy,
)


def _generate_synthetic_prices(
    tickers: tuple[str, ...],
    length: int = 300,
    trends: dict[str, Decimal] | None = None,
) -> dict[str, list[Decimal]]:
    """Generate deterministic synthetic price history for tests."""
    trends = trends or {}
    prices: dict[str, list[Decimal]] = {}
    for t in tickers:
        base = Decimal("100.00")
        step = trends.get(t, Decimal("0.05"))
        series: list[Decimal] = []
        curr = base
        for _ in range(length):
            curr += step
            series.append(curr)
        prices[t] = series
    return prices


def test_strategy_insufficient_history_raises() -> None:
    strat = VAR117Strategy()
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=100)
    with pytest.raises(ValueError, match="Insufficient history"):
        strat.compute_allocation(prices)


def test_strategy_selects_top_k_by_momentum() -> None:
    # SPY (+0.20/bar), QQQ (+0.50/bar), IWM (+0.30/bar), others flat (+0.01)
    trends = {
        "QQQ": Decimal("0.50"),
        "IWM": Decimal("0.30"),
        "SPY": Decimal("0.20"),
    }
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat = VAR117Strategy(top_k=3)
    signal = strat.compute_allocation(prices, as_of_date=dt.date(2026, 1, 1))

    assert signal.top_assets == ["QQQ", "IWM", "SPY"]
    assert signal.target_weights["QQQ"] > Decimal("0")
    assert signal.target_weights["IWM"] > Decimal("0")
    assert signal.target_weights["SPY"] > Decimal("0")

    # Non-selected assets must have zero weight
    assert signal.target_weights["GLD"] == Decimal("0")
    assert signal.target_weights["TLT"] == Decimal("0")


def test_strategy_cash_filter_zeros_negative_momentum() -> None:
    # Set all other assets to -0.50 so SPY (-0.10) is guaranteed 3rd
    trends = {t: Decimal("-0.50") for t in DEFAULT_UNIVERSE}
    trends["QQQ"] = Decimal("0.50")
    trends["IWM"] = Decimal("0.20")
    trends["SPY"] = Decimal("-0.10")

    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat = VAR117Strategy(top_k=3, cash_filter=True)
    signal = strat.compute_allocation(prices)

    assert "SPY" in signal.top_assets
    assert signal.target_weights["SPY"] == Decimal("0")
    assert signal.cash_weight > Decimal("0")

    # Verify score table has FAIL for SPY
    spy_score = next(s for s in signal.scores if s.ticker == "SPY")
    assert spy_score.cash_filter_status == "FAIL"


def test_strategy_all_negative_momentum_yields_full_cash() -> None:
    # All assets trending downwards
    trends = {t: Decimal("-0.10") for t in DEFAULT_UNIVERSE}
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat = VAR117Strategy(cash_filter=True)
    signal = strat.compute_allocation(prices)

    for t in DEFAULT_UNIVERSE:
        assert signal.target_weights[t] == Decimal("0")

    assert signal.cash_weight == Decimal("1.0000")


def test_strategy_weights_and_cash_sum_to_one() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    strat = VAR117Strategy()
    signal = strat.compute_allocation(prices)

    total_weight = sum(signal.target_weights.values()) + signal.cash_weight
    assert total_weight == pytest.approx(Decimal("1.0000"), abs=Decimal("0.001"))


def test_strategy_scores_decimal_types() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    strat = VAR117Strategy()
    signal = strat.compute_allocation(prices)

    for score in signal.scores:
        assert isinstance(score, TickerScore)
        assert isinstance(score.close_price, Decimal)
        assert isinstance(score.mom_9m_pct, Decimal)
        assert isinstance(score.vol_12m_pct, Decimal)
        assert isinstance(score.target_weight, Decimal)

    assert isinstance(signal.signal_hash, str)
    assert len(signal.signal_hash) == 64  # SHA-256


def test_strategy_invested_weights_never_exceed_one() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    strat = VAR117Strategy()
    signal = strat.compute_allocation(prices)

    invested_sum = sum(signal.target_weights.values())
    assert invested_sum <= Decimal("1.0000")
    assert signal.cash_weight >= Decimal("0.0000")


def test_strategy_tie_break_alphabetical() -> None:
    # All identical prices -> identical momentum 0
    trends = {t: Decimal("0.00") for t in DEFAULT_UNIVERSE}
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat = VAR117Strategy(top_k=3, cash_filter=False)
    signal = strat.compute_allocation(prices)

    # Alphabetical order: DBC, EEM, EFA, GLD, IEF, IWM, LQD, QQQ, SHY, SPY, TLT, VNQ
    assert signal.top_assets == ["DBC", "EEM", "EFA"]
