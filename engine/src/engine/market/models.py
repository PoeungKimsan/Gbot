"""Immutable, all-Decimal primitives for quotes and bars.

Design rules enforced here:

* **No floats, ever.** Every price is a :class:`~decimal.Decimal`. OANDA transmits prices
  as JSON *strings*, and they stay strings until they reach a Decimal constructor.
* **UTC in, UTC out.** ``timestamp_utc`` is normalized to UTC on construction, so every
  downstream comparison and bucketing is offset-free.
* **Strategy reads mid; fills read bid/ask.** ``Bar`` stores both sides and derives
  ``mid_ohlc`` on demand, so no caller can accidentally simulate a fill against mid.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "OHLC",
    "Bar",
    "Quote",
    "Timeframe",
    "duration_of",
    "mid_of",
    "ohlc_from_prices",
    "quantize_to",
]


class Timeframe(StrEnum):
    """Supported bar granularities.

    Members are simultaneously the wire name and a comparable string, so OANDA
    granularities serialize directly. ``from_value`` returns ``None`` for anything
    unsupported, so a new upstream granularity degrades into "not handled" rather than
    being silently mis-built.
    """

    M1 = "M1"
    M5 = "M5"

    @property
    def duration(self) -> dt.timedelta:
        return duration_of(self)

    @classmethod
    def from_value(cls, value: Any) -> Timeframe | None:  # noqa: ANN401
        """Parse an OANDA granularity name, returning ``None`` when unsupported."""
        if not isinstance(value, str):
            return None
        try:
            return cls(value.strip().upper())
        except ValueError:
            return None


_DURATIONS: Final[dict[str, dt.timedelta]] = {
    Timeframe.M1: dt.timedelta(minutes=1),
    Timeframe.M5: dt.timedelta(minutes=5),
}


def duration_of(timeframe: Any) -> dt.timedelta:  # noqa: ANN401 - validates below
    """Duration of a supported timeframe; raises for anything else.

    Accepts the ``Timeframe`` enum or any value; anything unsupported raises, which is
    what makes an unknown granularity fail loudly instead of defaulting.
    """
    try:
        return _DURATIONS[timeframe]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"unsupported timeframe {timeframe!r}") from exc


def _as_decimal(
    value: Any, *, field: str, allow_zero: bool = True  # noqa: ANN401
) -> Decimal:
    """Coerce a price to an exact Decimal, rejecting floats outright.

    A float carrying more than the representable precision is silently truncated by
    ``Decimal(str(...))``, which would corrupt a price without any visible symptom. The
    only sanctioned route into the numeric core is this function.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a Decimal, got bool")
    if isinstance(value, float):
        raise ValueError(f"{field} must be a Decimal, got float {value!r}")
    if isinstance(value, Decimal):
        converted = value
    elif isinstance(value, int):
        converted = Decimal(value)
    elif isinstance(value, str):
        try:
            converted = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{field} is not a parseable Decimal: {value!r}") from exc
    else:
        raise ValueError(f"{field} must be a Decimal, got {type(value).__name__}")

    if not converted.is_finite():
        raise ValueError(f"{field} must be finite, got {converted!r}")
    if not allow_zero and converted == 0:
        raise ValueError(f"{field} must be non-zero")
    return converted


def _require_utc(
    timestamp: Any, *, field: str = "timestamp_utc"  # noqa: ANN401
) -> dt.datetime:
    """Normalize an aware datetime to UTC, rejecting naive input."""
    if not isinstance(timestamp, dt.datetime):
        raise ValueError(f"{field} must be a datetime, got {type(timestamp).__name__}")
    if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
        raise ValueError(f"{field} must be timezone-aware, got a naive datetime {timestamp!r}")
    return timestamp.astimezone(dt.UTC)


@dataclass(frozen=True, slots=True)
class Quote:
    """A single two-sided price observation.

    ``bid`` and ``ask`` are the tradable book, not the display venue. A zero spread is
    rejected because it means a crossed or frozen book rather than a quotable market.
    """

    bid: Decimal
    ask: Decimal
    timestamp_utc: dt.datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "bid", _as_decimal(self.bid, field="bid"))
        object.__setattr__(self, "ask", _as_decimal(self.ask, field="ask"))
        object.__setattr__(self, "timestamp_utc", _require_utc(self.timestamp_utc))

        if self.bid <= 0:
            raise ValueError(f"bid must be positive, got {self.bid!r}")
        if self.ask <= 0:
            raise ValueError(f"ask must be positive, got {self.ask!r}")
        if self.ask < self.bid:
            raise ValueError(f"ask {self.ask!r} must not be below bid {self.bid!r}")
        if self.ask == self.bid:
            raise ValueError("ask must not equal bid; a zero spread is not a quotable market")

    @classmethod
    def from_bid_ask_strings(cls, bid: str, ask: str, timestamp_utc: dt.datetime) -> Quote:
        """Build from OANDA's string-typed prices without touching a float."""
        return cls(
            bid=Decimal(bid),
            ask=Decimal(ask),
            timestamp_utc=timestamp_utc,
        )

    @property
    def spread(self) -> Decimal:
        """Raw bid/ask spread in price units."""
        return self.ask - self.bid

    @property
    def mid(self) -> Decimal:
        return mid_of(self.bid, self.ask)


@dataclass(frozen=True, slots=True)
class OHLC:
    """Four Decimal prices constrained to be a real bar."""

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        for field in ("open", "high", "low", "close"):
            object.__setattr__(self, field, _as_decimal(getattr(self, field), field=field))
        if self.high < self.low:
            raise ValueError(f"high {self.high!r} must not be below low {self.low!r}")
        if self.high < self.open:
            raise ValueError(f"open {self.open!r} must not exceed high {self.high!r}")
        if self.high < self.close:
            raise ValueError(f"close {self.close!r} must not exceed high {self.high!r}")
        if self.low > self.open:
            raise ValueError(f"open {self.open!r} must not be below low {self.low!r}")
        if self.low > self.close:
            raise ValueError(f"close {self.close!r} must not be below low {self.low!r}")


@dataclass(frozen=True, slots=True)
class Bar:
    """A completed or in-progress OHLC bar for one timeframe.

    ``bid_ohlc`` and ``ask_ohlc`` are the tradable sides. ``mid_ohlc`` is derived, never
    stored: strategy logic reads it, fill simulation reads the raw sides, and the two can
    never drift apart because they are computed from the same fields.
    """

    timeframe: str
    timestamp_utc: dt.datetime
    complete: bool
    volume: int
    bid_ohlc: OHLC
    ask_ohlc: OHLC

    def __post_init__(self) -> None:
        duration_of(self.timeframe)
        object.__setattr__(self, "timestamp_utc", _require_utc(self.timestamp_utc))

        if not isinstance(self.complete, bool):
            raise ValueError(f"complete must be a bool, got {type(self.complete).__name__}")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int):
            raise ValueError(f"volume must be an int, got {type(self.volume).__name__}")
        if self.volume < 0:
            raise ValueError(f"volume must not be negative, got {self.volume}")

        for field in ("bid_ohlc", "ask_ohlc"):
            value = getattr(self, field)
            if not isinstance(value, OHLC):
                raise ValueError(f"{field} must be an OHLC, got {type(value).__name__}")

        self._reject_crossed_book()

    def _reject_crossed_book(self) -> None:
        """Every ask price must sit at or above the corresponding bid price."""
        rows = (
            ("open", self.bid_ohlc.open, self.ask_ohlc.open),
            ("high", self.bid_ohlc.high, self.ask_ohlc.high),
            ("low", self.bid_ohlc.low, self.ask_ohlc.low),
            ("close", self.bid_ohlc.close, self.ask_ohlc.close),
        )
        for name, bid, ask in rows:
            if ask < bid:
                raise ValueError(
                    f"ask {name} {ask!r} is below bid {name} {bid!r}; the bar implies a "
                    "crossed or inverted book"
                )

    @property
    def mid_ohlc(self) -> OHLC:
        """Mid-side OHLC, computed from bid and ask on every access."""
        return OHLC(
            open=mid_of(self.bid_ohlc.open, self.ask_ohlc.open),
            high=mid_of(self.bid_ohlc.high, self.ask_ohlc.high),
            low=mid_of(self.bid_ohlc.low, self.ask_ohlc.low),
            close=mid_of(self.bid_ohlc.close, self.ask_ohlc.close),
        )

    @property
    def spread_ohlc(self) -> OHLC:
        """Per-side spread, useful for spread monitoring and fill assumptions."""
        return OHLC(
            open=self.ask_ohlc.open - self.bid_ohlc.open,
            high=self.ask_ohlc.high - self.bid_ohlc.high,
            low=self.ask_ohlc.low - self.bid_ohlc.low,
            close=self.ask_ohlc.close - self.bid_ohlc.close,
        )

    def __lt__(self, other: Bar) -> bool:
        return self.timestamp_utc < other.timestamp_utc


def mid_of(bid: Decimal, ask: Decimal) -> Decimal:
    """Mid price, rounded half-even to the coarser of the two input precisions.

    Rounding to the *coarser* exponent is deliberate: a finer result would imply precision
    the inputs never had, and a half-even tie-break keeps the operation deterministic.
    """
    if ask < bid:
        raise ValueError(f"ask {ask!r} must not be below bid {bid!r}")

    exponent = min(bid.as_tuple().exponent, ask.as_tuple().exponent)
    midpoint = (bid + ask) / Decimal(2)
    if midpoint.as_tuple().exponent == exponent:
        return midpoint
    return midpoint.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_EVEN)


def quantize_to(value: Decimal, grid: Decimal) -> Decimal:
    """Snap ``value`` onto a ``grid`` (for example an instrument's tick)."""
    return value.quantize(grid, rounding=ROUND_HALF_EVEN)


def ohlc_from_prices(prices: Iterable[Decimal]) -> OHLC:
    """Aggregate an iterable of Decimal prices into a bar.

    Order matters for open and close: the first price seen is the open, the last is the
    close. Intended for building bid/ask bars from a quote stream.
    """
    materialized = list(prices)
    if not materialized:
        raise ValueError("cannot build an OHLC bar from zero prices")
    return OHLC(
        open=materialized[0],
        high=max(materialized),
        low=min(materialized),
        close=materialized[-1],
    )
