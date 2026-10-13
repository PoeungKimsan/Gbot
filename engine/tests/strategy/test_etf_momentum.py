"""Unit tests for VAR117Strategy (Multi-Asset ETF Momentum Rotation)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from engine.market.nyse_calendar import get_nyse_trading_days
from engine.strategy.etf_momentum import (
    DEFAULT_UNIVERSE,
    TickerScore,
    VAR117Strategy,
)

CANONICAL_AS_OF = dt.date(2025, 12, 31)


def _generate_synthetic_prices(
    tickers: tuple[str, ...] = DEFAULT_UNIVERSE,
    length: int = 300,
    trends: dict[str, Decimal] | None = None,
    as_of_date: dt.date = CANONICAL_AS_OF,
) -> dict[str, list[tuple[dt.date, Decimal]]]:
    """Generate deterministic synthetic dated price history for tests."""
    trends = trends or {}
    all_trading_days = get_nyse_trading_days(dt.date(2024, 1, 1), as_of_date)
    trading_days = all_trading_days[-length:]
    prices: dict[str, list[tuple[dt.date, Decimal]]] = {}
    for t in tickers:
        base = Decimal("500.00")
        step = trends.get(t, Decimal("0.05"))
        series: list[tuple[dt.date, Decimal]] = []
        curr = base
        for d in trading_days:
            curr += step
            series.append((d, curr))
        prices[t] = series
    return prices


def test_strategy_insufficient_history_raises() -> None:
    strat = VAR117Strategy()
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=100)
    with pytest.raises(ValueError, match="Insufficient history"):
        strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)


def test_strategy_selects_top_k_by_momentum() -> None:
    # SPY (+0.20/bar), QQQ (+0.50/bar), IWM (+0.30/bar), others flat (+0.01)
    trends = {
        "QQQ": Decimal("0.50"),
        "IWM": Decimal("0.30"),
        "SPY": Decimal("0.20"),
    }
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat = VAR117Strategy(top_k=3)
    signal = strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    assert signal.top_assets == ["QQQ", "IWM", "SPY"]
    assert signal.target_weights["QQQ"] > Decimal("0")
    assert signal.target_weights["IWM"] > Decimal("0")
    assert signal.target_weights["SPY"] > Decimal("0")

    # Non-selected assets must have zero weight
    assert signal.target_weights["GLD"] == Decimal("0")
    assert signal.target_weights["TLT"] == Decimal("0")


def test_strategy_cash_filter_zeros_negative_momentum() -> None:
    """Under descending momentum ranking (M_rank1 >= M_rank2 >= M_rank3),
    only 4 failure states exist:
    1. None fail: M_rank1 >= M_rank2 >= M_rank3 > 0.
    2. Rank 3 fails: M_rank1 >= M_rank2 > 0 >= M_rank3.
    3. Ranks 2+3 fail: M_rank1 > 0 >= M_rank2 >= M_rank3.
    4. All fail: 0 >= M_rank1 >= M_rank2 >= M_rank3.

    Any scenario where a higher rank fails while a lower rank passes (e.g. Rank 1 fails
    while Rank 2 passes) is mathematically impossible because M_rank1 >= M_rank2.
    """
    # Set all other assets to -0.50 so SPY (-0.10) is guaranteed 3rd
    trends = {t: Decimal("-0.50") for t in DEFAULT_UNIVERSE}
    trends["QQQ"] = Decimal("0.50")
    trends["IWM"] = Decimal("0.20")
    trends["SPY"] = Decimal("-0.10")

    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat_cf = VAR117Strategy(top_k=3, cash_filter=True)
    strat_no_cf = VAR117Strategy(top_k=3, cash_filter=False)

    signal_cf = strat_cf.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)
    signal_no_cf = strat_no_cf.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    # Identical top assets selected
    assert signal_cf.top_assets == signal_no_cf.top_assets == ["QQQ", "IWM", "SPY"]

    # Survivors (QQQ, IWM) must retain identical weights to unfiltered (tol 0.0001)
    assert signal_cf.target_weights["QQQ"] == pytest.approx(
        signal_no_cf.target_weights["QQQ"], abs=Decimal("0.0001")
    )
    assert signal_cf.target_weights["IWM"] == pytest.approx(
        signal_no_cf.target_weights["IWM"], abs=Decimal("0.0001")
    )

    # Failed asset receives 0
    assert signal_cf.target_weights["SPY"] == Decimal("0.0000")

    # Cash must exactly equal the failed raw weight of SPY (tol 0.0001)
    failed_raw_weight = signal_no_cf.target_weights["SPY"]
    assert signal_cf.cash_weight == pytest.approx(failed_raw_weight, abs=Decimal("0.0001"))

    # Verify score table has FAIL for SPY and PASS for survivors
    spy_score = next(s for s in signal_cf.scores if s.ticker == "SPY")
    qqq_score = next(s for s in signal_cf.scores if s.ticker == "QQQ")
    iwm_score = next(s for s in signal_cf.scores if s.ticker == "IWM")
    assert spy_score.cash_filter_status == "FAIL"
    assert qqq_score.cash_filter_status == "PASS"
    assert iwm_score.cash_filter_status == "PASS"


def test_strategy_cash_filter_none_fail_preserves_unfiltered_weights() -> None:
    """When all top assets pass the cash filter, weights are identical to unfiltered."""
    # All top assets have positive momentum
    trends = {t: Decimal("-0.10") for t in DEFAULT_UNIVERSE}
    trends["QQQ"] = Decimal("0.50")
    trends["IWM"] = Decimal("0.30")
    trends["SPY"] = Decimal("0.20")

    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat_cf = VAR117Strategy(top_k=3, cash_filter=True)
    strat_no_cf = VAR117Strategy(top_k=3, cash_filter=False)

    signal_cf = strat_cf.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)
    signal_no_cf = strat_no_cf.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    assert signal_cf.top_assets == signal_no_cf.top_assets == ["QQQ", "IWM", "SPY"]
    for sym in ["QQQ", "IWM", "SPY"]:
        assert signal_cf.target_weights[sym] == pytest.approx(
            signal_no_cf.target_weights[sym], abs=Decimal("0.0001")
        )

    # Cash is zero (or minor rounding residual)
    assert signal_cf.cash_weight == pytest.approx(signal_no_cf.cash_weight, abs=Decimal("0.0001"))


def test_strategy_cash_filter_rank2_and_rank3_fail() -> None:
    # Only QQQ (+0.50) has positive momentum; IWM (-0.05) and SPY (-0.10) fail
    trends = {t: Decimal("-0.50") for t in DEFAULT_UNIVERSE}
    trends["QQQ"] = Decimal("0.50")
    trends["IWM"] = Decimal("-0.05")
    trends["SPY"] = Decimal("-0.10")

    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat_cf = VAR117Strategy(top_k=3, cash_filter=True)
    strat_no_cf = VAR117Strategy(top_k=3, cash_filter=False)

    signal_cf = strat_cf.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)
    signal_no_cf = strat_no_cf.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    assert signal_cf.top_assets == signal_no_cf.top_assets == ["QQQ", "IWM", "SPY"]

    # Survivor (QQQ) retains unchanged unfiltered weight
    assert signal_cf.target_weights["QQQ"] == pytest.approx(
        signal_no_cf.target_weights["QQQ"], abs=Decimal("0.0001")
    )

    # Failed assets (IWM, SPY) get zero
    assert signal_cf.target_weights["IWM"] == Decimal("0.0000")
    assert signal_cf.target_weights["SPY"] == Decimal("0.0000")

    # Cash equals the sum of failed shares
    failed_raw_sum = (
        signal_no_cf.target_weights["IWM"] + signal_no_cf.target_weights["SPY"]
    )
    assert signal_cf.cash_weight == pytest.approx(failed_raw_sum, abs=Decimal("0.0001"))


def test_strategy_cash_filter_all_negative_momentum_yields_full_cash() -> None:
    # All assets trending downwards
    trends = {t: Decimal("-0.10") for t in DEFAULT_UNIVERSE}
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat = VAR117Strategy(cash_filter=True)
    signal = strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    for t in DEFAULT_UNIVERSE:
        assert signal.target_weights[t] == Decimal("0")

    assert signal.cash_weight == Decimal("1.0000")


def test_strategy_weights_and_cash_sum_to_one() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    strat = VAR117Strategy()
    signal = strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    total_weight = sum(signal.target_weights.values()) + signal.cash_weight
    assert total_weight == pytest.approx(Decimal("1.0000"), abs=Decimal("0.001"))


def test_strategy_scores_decimal_types() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    strat = VAR117Strategy()
    signal = strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

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
    signal = strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    invested_sum = sum(signal.target_weights.values())
    assert invested_sum <= Decimal("1.0000")
    assert signal.cash_weight >= Decimal("0.0000")


def test_strategy_tie_break_alphabetical() -> None:
    # All identical prices -> identical momentum 0
    trends = {t: Decimal("0.00") for t in DEFAULT_UNIVERSE}
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300, trends=trends)
    strat = VAR117Strategy(top_k=3, cash_filter=False)
    # To test pure momentum tie-break with non-zero volatility, add tiny jitter to price history
    # but same end-to-end ratio
    for t in DEFAULT_UNIVERSE:
        d_series = prices[t]
        # modify middle point slightly so vol is non-zero
        mid_d, mid_p = d_series[100]
        d_series[100] = (mid_d, mid_p + Decimal("1.00"))
    signal = strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)

    # Alphabetical order: DBC, EEM, EFA, GLD, IEF, IWM, LQD, QQQ, SHY, SPY, TLT, VNQ
    assert signal.top_assets == ["DBC", "EEM", "EFA"]


def test_strategy_rejects_nonpositive_past_price() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    past_idx = -1 - 189
    d, _ = prices["SPY"][past_idx]
    prices["SPY"][past_idx] = (d, Decimal("0.00"))
    strat = VAR117Strategy()
    with pytest.raises(ValueError, match="Non-positive past price for SPY"):
        strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)


def test_strategy_rejects_nonpositive_prev_price() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    d, _ = prices["SPY"][-10]
    prices["SPY"][-10] = (d, Decimal("-1.00"))
    strat = VAR117Strategy()
    with pytest.raises(ValueError, match="Non-positive price for SPY in volatility window"):
        strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)


def test_flat_series() -> None:
    # SPY has completely flat prices (0 variance / zero volatility)
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    d_list = [d for d, _ in prices["SPY"]]
    prices["SPY"] = [(d, Decimal("100.00")) for d in d_list]
    strat = VAR117Strategy()
    with pytest.raises(ValueError, match="Zero or negative volatility computed for SPY"):
        strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)


def test_strategy_rejects_zero_volatility() -> None:
    test_flat_series()


def test_misaligned_dates() -> None:
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    all_trading_days = get_nyse_trading_days(dt.date(2024, 1, 1), CANONICAL_AS_OF)
    diff_days = all_trading_days[-301:-1]
    prices["QQQ"] = [(d, Decimal("100.00") + Decimal(i)) for i, d in enumerate(diff_days)]
    strat = VAR117Strategy()
    with pytest.raises(ValueError, match=r"Date sequence mismatch for QQQ"):
        strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)


def test_calendar_gap() -> None:
    """Detects missing NYSE trading session within the price series."""
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    # Remove one trading session from all tickers to create a gap
    for t in DEFAULT_UNIVERSE:
        prices[t].pop(150)
    strat = VAR117Strategy()
    with pytest.raises(ValueError, match=r"Gap against NYSE calendar detected"):
        strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)


def test_last_date_not_equal_as_of_date() -> None:
    """Fails when latest bar date does not match the requested as_of_date."""
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    strat = VAR117Strategy()
    # Canonical prices end on 2025-12-31; request 2025-12-30
    wrong_as_of = dt.date(2025, 12, 30)
    with pytest.raises(
        ValueError, match=r"Latest date in price history .* does not match as_of_date"
    ):
        strat.compute_allocation(prices, as_of_date=wrong_as_of)


def test_volatility_015_fallback() -> None:
    """Verifies 0.15 annualized volatility fallback when insufficient returns exist in window."""
    strat = VAR117Strategy()
    # Strategy with volatility_lookback_days=1 produces len(daily_rets) = 1 (n_rets <= 1)
    strat.volatility_lookback_days = 1
    prices = _generate_synthetic_prices(DEFAULT_UNIVERSE, length=300)
    signal = strat.compute_allocation(prices, as_of_date=CANONICAL_AS_OF)
    for score in signal.scores:
        assert score.vol_12m_pct == Decimal("15.00")

