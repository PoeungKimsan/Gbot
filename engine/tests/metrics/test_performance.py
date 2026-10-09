"""Tests for the reporting analytics.

``engine.metrics.performance`` is the one module allowed to hold floats, because it is
the reporting boundary: nothing downstream consumes these numbers as money. Everything in
it is therefore checked for *statistical* properties rather than arithmetic exactness,
with two exceptions that are exact and both matter:

* **Reproducibility.** The bootstrap uses a fixed seed and its own ``random.Random``
   instance, so a report is a pure function of the trades it is given. A report that
   changed between two runs of the same day would make the whole journal pointless.
* **Honesty about sample size.** Below 30 trades the confidence interval is not computed
   at all. A CI from 8 samples is not a wide interval, it is a fabrication.
"""

import datetime as dt
import random
from decimal import Decimal

import pytest

from engine.metrics.performance import (
    BOOTSTRAP_RESAMPLES,
    BOOTSTRAP_SEED,
    MINIMUM_SAMPLE_SIZE,
    STATUS_COMPLETE,
    STATUS_INSUFFICIENT_DATA,
    PerformanceInputError,
    TradeResult,
    compute_performance,
)

UTC = dt.UTC

#: A hand-checkable dataset: four winners, four losers, 8 trades.
MIXED = (
    TradeResult(trade_id="t1", pnl=Decimal("200"), r_multiple=Decimal("2")),
    TradeResult(trade_id="t2", pnl=Decimal("-100"), r_multiple=Decimal("-1")),
    TradeResult(trade_id="t3", pnl=Decimal("100"), r_multiple=Decimal("1")),
    TradeResult(trade_id="t4", pnl=Decimal("-100"), r_multiple=Decimal("-1")),
    TradeResult(trade_id="t5", pnl=Decimal("300"), r_multiple=Decimal("3")),
    TradeResult(trade_id="t6", pnl=Decimal("-100"), r_multiple=Decimal("-1")),
    TradeResult(trade_id="t7", pnl=Decimal("50"), r_multiple=Decimal("0.5")),
    TradeResult(trade_id="t8", pnl=Decimal("-200"), r_multiple=Decimal("-2")),
)


def _many(count: int) -> tuple[TradeResult, ...]:
    """A repeating pattern of one +1R winner and one -1R loser."""
    trades = []
    for index in range(count):
        if index % 2 == 0:
            trades.append(
                TradeResult(trade_id=f"w{index}", pnl=Decimal("100"), r_multiple=Decimal("1"))
            )
        else:
            trades.append(
                TradeResult(trade_id=f"l{index}", pnl=Decimal("-100"), r_multiple=Decimal("-1"))
            )
    return tuple(trades)


def _varied(count: int) -> tuple[TradeResult, ...]:
    """Symmetrically spread, incommensurate R multiples.

    A balanced +1/-1 pattern puts every bootstrap resample mean on one of a handful
    of values, which no seed can move. Multiples of 1/31 have no such coincidence,
    so the resample means spread out and a different seed genuinely moves the
    interval - which is what makes the seed worth testing.
    """
    return tuple(
        TradeResult(
            trade_id=f"v{index}",
            pnl=(Decimal(2 * index - count) / Decimal("31")) * Decimal("100"),
            r_multiple=Decimal(2 * index - count) / Decimal("31"),
        )
        for index in range(count)
    )


# --------------------------------------------------------------------------- #
# module contract
# --------------------------------------------------------------------------- #
def test_bootstrap_settings_are_the_specified_values() -> None:
    assert BOOTSTRAP_RESAMPLES == 10_000
    assert isinstance(BOOTSTRAP_SEED, int)


def test_the_ci_threshold_is_thirty_trades() -> None:
    assert MINIMUM_SAMPLE_SIZE == 30


# --------------------------------------------------------------------------- #
# point estimates
# --------------------------------------------------------------------------- #
def test_realized_pnl_is_the_sum_of_the_trades() -> None:
    report = compute_performance(MIXED)

    assert report.trade_count == 8
    # 200 - 100 + 100 - 100 + 300 - 100 + 50 - 200
    assert report.realized_pnl == 150.0


def test_win_rate_counts_only_profitable_trades() -> None:
    report = compute_performance(MIXED)

    assert report.win_rate == 0.5


def test_profit_factor_is_gross_profit_over_gross_loss() -> None:
    report = compute_performance(MIXED)

    assert report.profit_factor == pytest.approx(1.3)


def test_expectancy_in_r_is_the_mean_r_multiple() -> None:
    report = compute_performance(MIXED)

    assert report.expectancy_r == pytest.approx(0.1875)


def test_max_drawdown_is_the_deepest_peak_to_trough_decline() -> None:
    """Cumulative PnL runs 200, 100, 200, 100, 400, 300, 350, 150; the trough is 150."""
    report = compute_performance(MIXED)

    assert report.max_drawdown == 250.0


def test_max_drawdown_of_a_monotonically_rising_curve_is_zero() -> None:
    trades = tuple(
        TradeResult(trade_id=f"w{i}", pnl=Decimal("10"), r_multiple=Decimal("1"))
        for i in range(5)
    )

    report = compute_performance(trades)

    assert report.max_drawdown == 0.0


def test_the_equity_curve_follows_close_time_when_given() -> None:
    """Out-of-order input is sorted, so the drawdown is over the real sequence.

    Input order (+200, -100, -100) would peak at 200 and draw down 200. Sorted by
    close time (-100, +200, -100) the curve dips to -100, recovers to +100, and
    never falls from a peak by more than 100.
    """
    trades = (
        TradeResult(
            trade_id="win",
            pnl=Decimal("200"),
            r_multiple=Decimal("2"),
            closed_at=dt.datetime(2026, 1, 15, 22, 0, tzinfo=UTC),
        ),
        TradeResult(
            trade_id="loss1",
            pnl=Decimal("-100"),
            r_multiple=Decimal("-1"),
            closed_at=dt.datetime(2026, 1, 15, 21, 0, tzinfo=UTC),
        ),
        TradeResult(
            trade_id="loss2",
            pnl=Decimal("-100"),
            r_multiple=Decimal("-1"),
            closed_at=dt.datetime(2026, 1, 15, 23, 0, tzinfo=UTC),
        ),
    )

    report = compute_performance(trades)

    assert report.max_drawdown == 100.0


def test_a_dataset_with_no_losses_has_no_profit_factor() -> None:
    trades = tuple(
        TradeResult(trade_id=f"w{i}", pnl=Decimal("10"), r_multiple=Decimal("1"))
        for i in range(5)
    )

    report = compute_performance(trades)

    assert report.profit_factor is None


# --------------------------------------------------------------------------- #
# sample-size honesty
# --------------------------------------------------------------------------- #
def test_a_small_sample_returns_insufficient_data() -> None:
    report = compute_performance(MIXED)

    assert report.status == STATUS_INSUFFICIENT_DATA


def test_a_small_sample_still_reports_the_point_estimates() -> None:
    report = compute_performance(MIXED)

    assert report.realized_pnl == 150.0
    assert report.expectancy_r == pytest.approx(0.1875)
    assert report.win_rate == 0.5


def test_a_small_sample_has_no_confidence_bounds() -> None:
    report = compute_performance(MIXED)

    assert report.confidence_interval is None


def test_twenty_nine_trades_is_still_insufficient() -> None:
    report = compute_performance(_many(29))

    assert report.trade_count == 29
    assert report.status == STATUS_INSUFFICIENT_DATA
    assert report.confidence_interval is None


def test_thirty_trades_is_enough_for_a_confidence_interval() -> None:
    report = compute_performance(_many(30))

    assert report.trade_count == 30
    assert report.status == STATUS_COMPLETE
    assert report.confidence_interval is not None


def test_the_interval_brackets_the_point_estimate() -> None:
    report = compute_performance(_many(30))

    assert report.confidence_interval is not None
    assert report.confidence_interval.lower <= report.expectancy_r
    assert report.expectancy_r <= report.confidence_interval.upper


def test_an_empty_dataset_is_insufficient_data() -> None:
    report = compute_performance(())

    assert report.trade_count == 0
    assert report.status == STATUS_INSUFFICIENT_DATA
    assert report.confidence_interval is None
    assert report.realized_pnl == 0.0
    assert report.max_drawdown == 0.0
    assert report.win_rate is None
    assert report.expectancy_r is None


# --------------------------------------------------------------------------- #
# reproducibility
# --------------------------------------------------------------------------- #
def test_the_same_trades_give_the_same_interval_every_time() -> None:
    first = compute_performance(_many(30))
    second = compute_performance(_many(30))

    assert first.confidence_interval == second.confidence_interval


def test_the_interval_does_not_depend_on_global_random_state() -> None:
    random.seed(1234)
    random.random()
    random.random()

    report = compute_performance(_many(30))
    again = compute_performance(_many(30))

    assert report.confidence_interval == again.confidence_interval


def test_the_report_is_a_pure_function_of_the_trades() -> None:
    random.random()
    first = compute_performance(_many(40))
    for _ in range(10):
        random.random()
    second = compute_performance(_many(40))

    assert first.confidence_interval == second.confidence_interval


def test_a_different_seed_moves_the_interval() -> None:
    baseline = compute_performance(_varied(30))
    other = compute_performance(_varied(30), seed=BOOTSTRAP_SEED + 1)

    assert baseline.confidence_interval is not None
    assert other.confidence_interval != baseline.confidence_interval


def test_the_interval_still_brackets_the_estimate_on_a_varied_dataset() -> None:
    report = compute_performance(_varied(60))

    assert report.confidence_interval is not None
    assert report.confidence_interval.lower <= report.expectancy_r
    assert report.expectancy_r <= report.confidence_interval.upper


def test_a_wider_sample_gives_a_tighter_interval() -> None:
    narrow = compute_performance(_many(30)).confidence_interval
    wide = compute_performance(_many(30), resamples=2_000).confidence_interval

    assert narrow is not None and wide is not None
    # Fewer resamples cannot reduce sampling noise in the percentile estimates.
    assert (wide.upper - wide.lower) >= (narrow.upper - narrow.lower)


# --------------------------------------------------------------------------- #
# input handling
# --------------------------------------------------------------------------- #
def test_a_float_trade_is_accepted_at_this_boundary() -> None:
    """Floats are permitted here and nowhere else; the report is the sink."""
    trades = (
        TradeResult(trade_id="a", pnl=200.0, r_multiple=2.0),  # type: ignore[arg-type]
        TradeResult(trade_id="b", pnl=-100.0, r_multiple=-1.0),  # type: ignore[arg-type]
    )

    report = compute_performance(trades)

    assert report.realized_pnl == 100.0
    assert report.expectancy_r == 0.5


def test_a_non_finite_trade_is_refused() -> None:
    trades = (TradeResult(trade_id="a", pnl=Decimal("NaN"), r_multiple=Decimal("1")),)

    with pytest.raises(PerformanceInputError):
        compute_performance(trades)


def test_a_bool_r_multiple_is_refused() -> None:
    trades = (TradeResult(trade_id="a", pnl=Decimal("1"), r_multiple=True),)  # type: ignore[arg-type]

    with pytest.raises(PerformanceInputError):
        compute_performance(trades)


def test_an_unusable_resample_count_is_refused() -> None:
    with pytest.raises(PerformanceInputError):
        compute_performance(_many(30), resamples=0)


def test_a_report_is_immutable() -> None:
    report = compute_performance(_many(30))

    with pytest.raises(Exception):  # noqa: B017
        report.realized_pnl = 1.0  # type: ignore[misc]
