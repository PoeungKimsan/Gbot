"""Volatility and spread gating: decides whether a signal may be generated.

Two independent conditions block signal generation:

1. **Spread breaker.** The current spread exceeds ``spread_breaker_multiple`` times the
   rolling *median* spread. A median, not a mean: a single wide quote during a news spike
   must not silently poison the reference level the way it would poison an average.
2. **Blackout windows.** Active news blackouts, computed by
   :mod:`engine.news.calendar`.

Both are inert until they have enough samples to judge. That is deliberate: blocking on a
median of two observations would be noise, so an under-populated breaker stays permissive
and says so, rather than pretending to be informed.

All values are Decimals end to end, and the rolling ATR uses true range (which accounts for
a gap away from the previous close) rather than raw high-minus-low.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

__all__ = [
    "BreakerConfig",
    "BreakerDecision",
    "BreakerReason",
    "RollingATR",
    "RollingMedian",
    "SignalBreaker",
]


class BreakerReason(StrEnum):
    """Why signal generation was blocked."""

    SPREAD = "SPREAD"
    BLACKOUT = "BLACKOUT"


def _median(values: Sequence[Decimal]) -> Decimal:
    """Median of a non-empty sorted sequence, averaging the middle pair when even.

    The average is quantized to the coarser of the two inputs so no extra precision is
    invented.
    """
    if not values:
        raise ValueError("median of an empty sequence is undefined")
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    lower, upper = ordered[middle - 1], ordered[middle]
    exponent = min(lower.as_tuple().exponent, upper.as_tuple().exponent)
    midpoint = (lower + upper) / Decimal(2)
    if midpoint.as_tuple().exponent == exponent:
        return midpoint
    return midpoint.quantize(Decimal(1).scaleb(exponent))


class RollingATR:
    """Rolling average true range over a fixed window of completed bars.

    True range is ``max(high - low, |high - previous close|, |low - previous close|)``,
    which is what makes a gap away from the previous close register. Incomplete bars are
    ignored rather than counted as zero, which would quietly drag the average down.
    """

    def __init__(self, *, window: int) -> None:
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            raise ValueError(f"window must be a positive int, got {window!r}")
        self._window = window
        self._ranges: list[Decimal] = []
        self._previous_close: Decimal | None = None

    @property
    def window(self) -> int:
        return self._window

    @property
    def samples(self) -> int:
        return len(self._ranges)

    @property
    def value(self) -> Decimal | None:
        """Mean of the retained true ranges, or ``None`` when empty."""
        if not self._ranges:
            return None
        return sum(self._ranges) / Decimal(len(self._ranges))

    def add(self, bar: Any) -> None:  # noqa: ANN401
        """Record one bar. Incomplete bars and a missing mid side are skipped."""
        if not getattr(bar, "complete", True):
            return
        mid = getattr(bar, "mid_ohlc", None)
        if mid is None:
            raise ValueError("bar has no mid_ohlc")

        candidates: list[Decimal] = [mid.high - mid.low]
        if self._previous_close is not None:
            candidates.append(abs(mid.high - self._previous_close))
            candidates.append(abs(mid.low - self._previous_close))

        self._ranges.append(max(candidates))
        if len(self._ranges) > self._window:
            self._ranges.pop(0)
        self._previous_close = mid.close

    def reset(self) -> None:
        self._ranges.clear()
        self._previous_close = None


class RollingMedian:
    """Rolling median of observed spreads.

    The median is re-derominatized rather than maintained incrementally, because the window
    is small and correctness matters more than the saving.
    """

    def __init__(self, *, window: int) -> None:
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            raise ValueError(f"window must be a positive int, got {window!r}")
        self._window = window
        self._values: list[Decimal] = []

    @property
    def window(self) -> int:
        return self._window

    @property
    def samples(self) -> int:
        return len(self._values)

    @property
    def value(self) -> Decimal | None:
        if not self._values:
            return None
        return _median(tuple(self._values))

    def add(self, value: Decimal) -> None:
        if isinstance(value, bool) or not isinstance(value, Decimal):
            raise ValueError(f"value must be a Decimal, got {type(value).__name__}")
        if not value.is_finite() or value < 0:
            raise ValueError(f"value must be a non-negative finite Decimal, got {value!r}")
        self._values.append(value)
        if len(self._values) > self._window:
            self._values.pop(0)

    @property
    def latest(self) -> Decimal | None:
        """The most recently added value, or ``None`` when empty."""
        if not self._values:
            return None
        return self._values[-1]

    def reset(self) -> None:
        self._values.clear()


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    """Thresholds for the signal breaker.

    ``spread_breaker_multiple`` is parsed from a string so a config file can never smuggle
    in a float, which would immediately corrupt the comparison it is used for.
    """

    spread_breaker_multiple: Decimal
    atr_window: int = 5
    spread_window: int = 60
    minimum_atr_bars: int = 2
    minimum_spread_samples: int = 5

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> BreakerConfig:
        """Build from a parsed YAML mapping, validating every field."""
        if not isinstance(raw, dict):
            raise ValueError(f"breaker config must be a mapping, got {type(raw).__name__}")

        multiple_raw = raw.get("spread_breaker_multiple", "3")
        if isinstance(multiple_raw, float):
            raise ValueError(
                f"spread_breaker_multiple must be a decimal string, got float {multiple_raw!r}"
            )
        try:
            multiple = Decimal(str(multiple_raw).strip())
        except Exception as exc:
            raise ValueError(
                f"spread_breaker_multiple is not a decimal: {multiple_raw!r}"
            ) from exc
        if not multiple.is_finite() or multiple <= 0:
            raise ValueError(
                f"spread_breaker_multiple must be positive, got {multiple!r}"
            )

        atr_window = cls._positive_int(raw, "atr_window_bars", default=5)
        spread_window = cls._positive_int(raw, "spread_window_samples", default=60)
        minimum_atr = cls._positive_int(raw, "minimum_atr_bars", default=2)
        minimum_spread = cls._positive_int(raw, "minimum_spread_samples", default=5)

        if minimum_atr > atr_window:
            raise ValueError(
                f"minimum_atr_bars ({minimum_atr}) cannot exceed atr_window_bars ({atr_window})"
            )
        if minimum_spread > spread_window:
            raise ValueError(
                f"minimum_spread_samples ({minimum_spread}) cannot exceed "
                f"spread_window_samples ({spread_window})"
            )

        return cls(
            spread_breaker_multiple=multiple,
            atr_window=atr_window,
            spread_window=spread_window,
            minimum_atr_bars=minimum_atr,
            minimum_spread_samples=minimum_spread,
        )

    @staticmethod
    def _positive_int(raw: Mapping[str, Any], key: str, *, default: int) -> int:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{key} must be a positive int, got {value!r}")
        return value


@dataclass(frozen=True, slots=True)
class BreakerDecision:
    """Whether signals may be generated right now, and why not if not."""

    blocked: bool
    reasons: tuple[BreakerReason, ...] = ()
    spread: Decimal | None = None
    median_spread: Decimal | None = None
    threshold: Decimal | None = None
    atr: Decimal | None = None
    active_blackouts: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def blackout(self) -> Any | None:  # noqa: ANN401
        """The first active blackout, for callers that only need one."""
        return self.active_blackouts[0] if self.active_blackouts else None


class SignalBreaker:
    """Stateful gate deciding whether a signal may be generated at a given instant."""

    def __init__(self, *, spec: Any, config: BreakerConfig) -> None:  # noqa: ANN401
        self._spec = spec
        self._config = config
        self._spreads = RollingMedian(window=config.spread_window)
        self._atr = RollingATR(window=config.atr_window)

    @property
    def config(self) -> BreakerConfig:
        return self._config

    @property
    def spreads(self) -> RollingMedian:
        return self._spreads

    @property
    def atr(self) -> RollingATR:
        return self._atr

    def observe_quote(self, quote: Any) -> None:  # noqa: ANN401
        """Record a quote's spread for the rolling median."""
        spread = self._spread_of(quote)
        self._spreads.add(spread)

    def observe_bar(self, bar: Any) -> None:  # noqa: ANN401
        """Record a completed bar for the rolling ATR."""
        self._atr.add(bar)

    def evaluate(
        self,
        timestamp_utc: dt.datetime,
        blackouts: Iterable[Any] | None = None,
    ) -> BreakerDecision:
        """Decide whether a signal may be generated at ``timestamp_utc``.

        Args:
            timestamp_utc: The instant being evaluated, always UTC.
            blackouts: Active blackout windows from :mod:`engine.news.calendar`.
        """
        if not isinstance(timestamp_utc, dt.datetime):
            raise ValueError("timestamp_utc must be a datetime")

        spread = self._latest_spread()
        median = self._spreads.value
        threshold = None
        reasons: list[BreakerReason] = []

        if median is not None and self._spreads.samples >= self._config.minimum_spread_samples:
            threshold = median * self._config.spread_breaker_multiple
            if spread is not None and spread > threshold:
                reasons.append(BreakerReason.SPREAD)

        active = [
            window for window in (blackouts or ()) if _contains(window, timestamp_utc)
        ]
        if active:
            reasons.append(BreakerReason.BLACKOUT)

        return BreakerDecision(
            blocked=bool(reasons),
            reasons=tuple(reasons),
            spread=spread,
            median_spread=median,
            threshold=threshold,
            atr=self._atr.value if self._atr.samples >= self._config.minimum_atr_bars else None,
            active_blackouts=tuple(active),
        )

    def reset(self) -> None:
        """Clear every accumulated observation."""
        self._spreads.reset()
        self._atr.reset()

    def _latest_spread(self) -> Decimal | None:
        return self._spreads.latest

    @staticmethod
    def _spread_of(quote: Any) -> Decimal:  # noqa: ANN401
        spread = getattr(quote, "spread", None)
        if not isinstance(spread, Decimal):
            raise ValueError("quote has no Decimal spread")
        return spread


def _contains(window: Any, timestamp_utc: dt.datetime) -> bool:  # noqa: ANN401
    return bool(window.contains(timestamp_utc))
