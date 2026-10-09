"""Reporting analytics: the one module where floats are permitted.

Every other package in the engine refuses a float, and this is the exception
``AGENTS.md`` 2.1 carves out: a report is the display and serialization boundary.
Nothing downstream consumes these numbers as money, so the reporting arithmetic uses
ordinary floats and accepts them as input.

Two rules still bind here, because they are what makes a report trustworthy rather
than merely pretty:

* **Reproducible.** The bootstrap uses a fixed seed and its own ``random.Random``
  instance, so a report is a pure function of the trades it is given. A number that
  changed between two runs of the same day would make the journal pointless.
* **Honest about sample size.** Below ``MINIMUM_SAMPLE_SIZE`` trades the confidence
  interval is not computed at all. A "wide interval" derived from eight resamples is
  not caution, it is a fabricated precision.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "MINIMUM_SAMPLE_SIZE",
    "STATUS_COMPLETE",
    "STATUS_INSUFFICIENT_DATA",
    "ConfidenceInterval",
    "PerformanceInputError",
    "PerformanceReport",
    "TradeResult",
    "compute_performance",
]

#: Below this trade count a confidence interval is not reported.
MINIMUM_SAMPLE_SIZE: Final[int] = 30

#: Resamples for the bootstrap interval. 10,000 is the usual floor for a 95%
#: interval whose endpoints are stable enough to print.
BOOTSTRAP_RESAMPLES: Final[int] = 10_000

#: The seed every bootstrap run uses, so the same trades always produce the same
#: interval. Fixed for the life of the report format: changing it would change
#: historical numbers with no change in the underlying data.
BOOTSTRAP_SEED: Final[int] = 20_260_115

#: A dataset large enough for a confidence interval to mean something.
STATUS_COMPLETE: Final[str] = "COMPLETE"

#: A dataset too small for a confidence interval. Point estimates are still reported.
STATUS_INSUFFICIENT_DATA: Final[str] = "INSUFFICIENT_DATA"

#: Confidence level the bootstrap interval is built for.
_CI_LOWER: Final[float] = 0.025
_CI_UPPER: Final[float] = 0.975


class PerformanceInputError(ValueError):
    """A trade, or a bootstrap parameter, is unusable."""


@dataclass(frozen=True, slots=True)
class TradeResult:
    """One closed trade, as the rest of the engine reports it.

    ``pnl`` and ``r_multiple`` are :class:`~decimal.Decimal` in the engine. This
    module converts them at its own boundary, so no other module ever has to.
    """

    trade_id: str
    pnl: Decimal
    r_multiple: Decimal
    closed_at: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    """A bootstrap interval for the expectancy in R."""

    lower: float
    upper: float

    @property
    def width(self) -> float:
        """How much the interval spans."""
        return self.upper - self.lower


@dataclass(frozen=True, slots=True)
class PerformanceReport:
    """The performance of a set of trades.

    ``expectancy_r`` is the mean R multiple; ``confidence_interval`` is the bootstrap
    interval around it, present only when the sample supports one.
    """

    status: str
    trade_count: int
    realized_pnl: float
    max_drawdown: float
    win_rate: float | None
    profit_factor: float | None
    expectancy_r: float | None
    confidence_interval: ConfidenceInterval | None


def compute_performance(
    trades: object,
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> PerformanceReport:
    """Compute the performance report for a set of closed trades.

    Args:
        trades: The trades, in any order. Those carrying ``closed_at`` are sorted by
            it, so the equity curve follows the sequence they actually closed in.
        seed: Seed for the bootstrap. Fixed by default, so a report is reproducible.
        resamples: Number of bootstrap resamples.

    Returns:
        The :class:`PerformanceReport`. When fewer than ``MINIMUM_SAMPLE_SIZE``
        trades are supplied the status is ``INSUFFICIENT_DATA`` and
        ``confidence_interval`` is ``None``.

    Raises:
        PerformanceInputError: if a trade is malformed, or ``resamples`` is not a
            positive integer.
    """
    if isinstance(trades, (str, bytes)) or not hasattr(trades, "__iter__"):
        raise PerformanceInputError(
            f"trades must be iterable, got {type(trades).__name__}"
        )
    resample_count = _validated_resamples(resamples)

    ordered = _ordered(trades)
    count = len(ordered)
    pnls = [_finite(entry.pnl, "pnl") for entry in ordered]
    multiples = [_finite(entry.r_multiple, "r_multiple") for entry in ordered]

    realized = math.fsum(pnls)
    drawdown = _max_drawdown(pnls)

    win_rate: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    interval: ConfidenceInterval | None = None
    status = STATUS_INSUFFICIENT_DATA

    if count:
        win_rate = sum(1 for value in multiples if value > 0) / count
        expectancy = math.fsum(multiples) / count

        gross_profit = math.fsum(value for value in pnls if value > 0)
        gross_loss = math.fsum(-value for value in pnls if value < 0)
        # No losers means the ratio is undefined, not infinite: printing "inf" in a
        # report would be a number no consumer can use.
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None

        if count >= MINIMUM_SAMPLE_SIZE:
            status = STATUS_COMPLETE
            interval = _bootstrap_interval(
                multiples, resamples=resample_count, seed=seed
            )

    return PerformanceReport(
        status=status,
        trade_count=count,
        realized_pnl=realized,
        max_drawdown=drawdown,
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy_r=expectancy,
        confidence_interval=interval,
    )


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #
def _ordered(trades: object) -> tuple[TradeResult, ...]:
    """Validate the trades and put them in the order they closed."""
    entries: list[TradeResult] = []
    for entry in trades:
        if not isinstance(entry, TradeResult):
            raise PerformanceInputError(
                f"trades must be TradeResult, got {type(entry).__name__}"
            )
        entries.append(entry)

    dated = [entry for entry in entries if entry.closed_at is not None]
    if len(dated) == len(entries):
        entries.sort(key=lambda entry: entry.closed_at)
    return tuple(entries)


def _validated_resamples(resamples: object) -> int:
    if isinstance(resamples, bool) or not isinstance(resamples, int):
        raise PerformanceInputError(
            f"resamples must be an int, got {type(resamples).__name__}"
        )
    if resamples < 1:
        raise PerformanceInputError(f"resamples must be at least 1, got {resamples}")
    return resamples


def _finite(value: object, field: str) -> float:
    """Convert a trade's Decimal to a float, refusing anything non-finite.

    ``float()`` is the point of this helper, and it is the only place in the engine
    where a Decimal becomes a float: here, at the reporting boundary.
    """
    if isinstance(value, bool):
        raise PerformanceInputError(f"{field} must not be a bool")
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise PerformanceInputError(
            f"{field} must be a number, got {type(value).__name__}"
        ) from exc
    if not math.isfinite(converted):
        raise PerformanceInputError(f"{field} must be finite, got {value!r}")
    return converted


def _max_drawdown(pnls: list[float]) -> float:
    """The deepest peak-to-trough decline of the cumulative PnL curve.

    The curve starts at zero, so a dataset that only ever rises has a drawdown of
    exactly zero rather than an undefined one.
    """
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _bootstrap_interval(
    values: list[float], *, resamples: int, seed: int
) -> ConfidenceInterval:
    """Bootstrap a confidence interval for the mean of ``values``.

    Each resample draws ``len(values)`` values with replacement and takes their
    mean; the interval is the 2.5th and 97.5th percentiles of those means. Sampling
    with replacement is what makes the interval an estimate of the spread of the
    mean, which is the quantity the report is about.
    """
    if not values:
        raise PerformanceInputError("a bootstrap interval needs at least one trade")

    # A private generator, so the interval depends only on the seed and never on
    # whatever else consumed the process's global random state.
    rng = random.Random(seed)  # noqa: S311 - resampling is not a security decision
    count = len(values)

    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(count):
            total += values[rng.randrange(count)]
        means.append(total / count)
    means.sort()

    return ConfidenceInterval(
        lower=_percentile(means, _CI_LOWER), upper=_percentile(means, _CI_UPPER)
    )


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """The ``fraction`` percentile of an already sorted list, linearly interpolated."""
    if not sorted_values:
        raise PerformanceInputError("a percentile needs at least one value")
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = fraction * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]

    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
