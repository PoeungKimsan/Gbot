"""Per-trading-day profit and loss, anchored to 17:00 America/New_York.

"Daily" has to mean something to a market, not to a calendar. XAUUSD trades for
almost the whole day, so the boundary that matters is the 17:00 New York close, and
this module defines a trading day as the 24 hours that *begin* at that instant.

Two details are deliberate:

* **Wall clock, not a fixed UTC hour.** New York is UTC-5 in winter and UTC-4 in
  summer, so the boundary moves. It is computed in the pinned ``tzdata`` zone
  (AGENTS.md 2.6), which makes the transition weekends come out right: the trading
  day containing the spring-forward is 23 real hours long and the one containing the
  fall-back is 25.
* **Realized accumulates, unrealized is a mark.** Realized PnL is a history and
  adds up. Unrealized PnL is the current value of what is still open, so a new mark
  *replaces* the previous one. Treating it as an accumulation would double-count
  every tick the market moved.

A trading day here is purely calendar-based: it does not know about weekends or
holidays, because that is the session layer's business and pretending otherwise
would silently drop a Saturday's loss.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from engine.risk.rounding import RoundingError, require_decimal

__all__ = [
    "DAILY_ROLLOVER",
    "NEW_YORK",
    "DailyPnLTracker",
    "TradingDayTotals",
    "trading_day_bounds",
    "trading_day_for",
]

#: The New York session close. A trading day starts here and runs to the same
#: wall-clock instant the next calendar day.
DAILY_ROLLOVER: Final[dt.time] = dt.time(17, 0)

#: The pinned zone. Never the host's local time (AGENTS.md 2.6).
NEW_YORK: Final[ZoneInfo] = ZoneInfo("America/New_York")

_ONE_DAY: Final[dt.timedelta] = dt.timedelta(days=1)


def _require_zone(value: object, field: str) -> dt.tzinfo:
    if not isinstance(value, dt.tzinfo):
        raise ValueError(f"{field} must be a tzinfo, got {type(value).__name__}")
    return value


def _require_time(value: object, field: str) -> dt.time:
    if not isinstance(value, dt.time):
        raise ValueError(f"{field} must be a time, got {type(value).__name__}")
    return value


def _require_instant(value: object, field: str) -> dt.datetime:
    if not isinstance(value, dt.datetime):
        raise ValueError(f"{field} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware, got {value!r}")
    return value


def _require_day(value: object, field: str) -> dt.date:
    # datetime is a date subclass, and accepting one here would silently truncate
    # a timestamp to its calendar date and lose the time of day.
    if isinstance(value, dt.datetime) or not isinstance(value, dt.date):
        raise ValueError(f"{field} must be a date, got {type(value).__name__}")
    return value


def trading_day_for(
    timestamp: dt.datetime,
    *,
    tz: dt.tzinfo = NEW_YORK,
    rollover: dt.time = DAILY_ROLLOVER,
) -> dt.date:
    """The trading day that ``timestamp`` belongs to.

    The day is named by the calendar date on which it started, so an event at
    18:00 New York on Thursday belongs to the Thursday trading day even though
    Thursday's *session* is nearly over.

    Args:
        timestamp: A timezone-aware instant.
        tz: The zone the rollover is expressed in.
        rollover: The local time a trading day starts.

    Returns:
        The trading day's date.

    Raises:
        ValueError: if the timestamp is naive, or the zone or rollover is of the
            wrong type.
    """
    instant = _require_instant(timestamp, "timestamp")
    zone = _require_zone(tz, "tz")
    boundary = _require_time(rollover, "rollover")

    local = instant.astimezone(zone)
    if local.time() >= boundary:
        return local.date()
    return local.date() - _ONE_DAY


def trading_day_bounds(
    day: dt.date,
    *,
    tz: dt.tzinfo = NEW_YORK,
    rollover: dt.time = DAILY_ROLLOVER,
) -> tuple[dt.datetime, dt.datetime]:
    """The UTC half-open interval ``[start, end)`` of a trading day.

    Both ends are resolved as New York wall clock, so across a DST transition the
    interval is 23 or 25 hours of real time rather than a fixed 24.

    Args:
        day: The trading day's date.
        tz: The zone the rollover is expressed in.
        rollover: The local time a trading day starts.

    Returns:
        ``(start_utc, end_utc)``.

    Raises:
        ValueError: if the day, zone, or rollover is of the wrong type.
    """
    trading_day = _require_day(day, "day")
    zone = _require_zone(tz, "tz")
    boundary = _require_time(rollover, "rollover")

    start = dt.datetime.combine(trading_day, boundary, tzinfo=zone).astimezone(dt.UTC)
    end = dt.datetime.combine(trading_day + _ONE_DAY, boundary, tzinfo=zone).astimezone(dt.UTC)
    return start, end


@dataclasses.dataclass(frozen=True, slots=True)
class TradingDayTotals:
    """Profit and loss for one trading day, in currency and in R.

    ``realized`` is the sum of closed trades; ``unrealized`` is the current mark of
    open ones. ``total`` is their sum, because equity is what is in the account plus
    what the open positions are worth right now.
    """

    day: dt.date
    realized: Decimal = Decimal(0)
    unrealized: Decimal = Decimal(0)
    realized_r: Decimal = Decimal(0)
    unrealized_r: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        _require_day(self.day, "day")
        for field in ("realized", "unrealized", "realized_r", "unrealized_r"):
            try:
                require_decimal(getattr(self, field), field=field)
            except RoundingError as exc:
                raise ValueError(str(exc)) from exc

    @property
    def total(self) -> Decimal:
        """Realized plus unrealized."""
        return self.realized + self.unrealized

    @property
    def total_r(self) -> Decimal:
        """The day's total in units of R."""
        return self.realized_r + self.unrealized_r


@dataclasses.dataclass(slots=True)
class _DayAccumulator:
    """Mutable running totals for one day, replaced by the public frozen type."""

    day: dt.date
    realized: Decimal = Decimal(0)
    unrealized: Decimal = Decimal(0)
    realized_r: Decimal = Decimal(0)
    unrealized_r: Decimal = Decimal(0)


class DailyPnLTracker:
    """Accumulates PnL into trading days.

    Instances are not thread-safe: the runtime owns one per engine run on the event
    loop, and the journal is what makes the history durable across processes.
    """

    def __init__(
        self,
        *,
        tz: dt.tzinfo = NEW_YORK,
        rollover: dt.time = DAILY_ROLLOVER,
    ) -> None:
        self._tz = _require_zone(tz, "tz")
        self._rollover = _require_time(rollover, "rollover")
        self._days: dict[dt.date, _DayAccumulator] = {}

    # -- reading ------------------------------------------------------------ #
    @property
    def days(self) -> tuple[dt.date, ...]:
        """Every day seen so far, in order."""
        return tuple(sorted(self._days))

    def totals(self, day: dt.date) -> TradingDayTotals | None:
        """Totals for ``day``, or ``None`` if nothing was recorded for it."""
        accumulator = self._days.get(_require_day(day, "day"))
        return None if accumulator is None else self._frozen(accumulator)

    def current(self, at: dt.datetime) -> TradingDayTotals:
        """Totals for the trading day that owns ``at``."""
        return self.totals(self.day_of(at)) or TradingDayTotals(day=self.day_of(at))

    def day_of(self, timestamp: dt.datetime) -> dt.date:
        """The trading day ``timestamp`` belongs to, under this tracker's anchor."""
        return trading_day_for(timestamp, tz=self._tz, rollover=self._rollover)

    # -- writing ------------------------------------------------------------ #
    def record_realized(
        self,
        amount: Decimal,
        at: dt.datetime,
        *,
        r_multiple: Decimal | None = None,
    ) -> TradingDayTotals:
        """Add a realized result to its trading day.

        Args:
            amount: Realized profit or loss, in currency.
            at: When it was realized.
            r_multiple: The trade's R multiple, when known.

        Returns:
            The day's totals after the update.
        """
        value = _validated(amount, field="amount")
        accumulator = self._accumulator_for(at)
        accumulator.realized += value
        if r_multiple is not None:
            accumulator.realized_r += _validated(r_multiple, field="r_multiple")
        return self._frozen(accumulator)

    def record_unrealized(
        self,
        amount: Decimal,
        at: dt.datetime,
        *,
        r_multiple: Decimal | None = None,
    ) -> TradingDayTotals:
        """Replace the open-position mark for the trading day.

        A mark is not a history: recording ``120`` and then ``35`` leaves the day
        marked at ``35``, not ``155``.

        Args:
            amount: The current mark of open positions.
            at: When it was observed.
            r_multiple: The mark in units of R, when known.

        Returns:
            The day's totals after the update.
        """
        value = _validated(amount, field="amount")
        accumulator = self._accumulator_for(at)
        accumulator.unrealized = value
        accumulator.unrealized_r = (
            Decimal(0) if r_multiple is None else _validated(r_multiple, field="r_multiple")
        )
        return self._frozen(accumulator)

    def record_r(
        self, r_multiple: Decimal, at: dt.datetime
    ) -> TradingDayTotals:
        """Record an R multiple with no currency amount attached.

        Args:
            r_multiple: The realized R multiple.
            at: When it was realized.

        Returns:
            The day's totals after the update.
        """
        value = _validated(r_multiple, field="r_multiple")
        accumulator = self._accumulator_for(at)
        accumulator.realized_r += value
        return self._frozen(accumulator)

    # -- internals ---------------------------------------------------------- #
    def _accumulator_for(self, at: dt.datetime) -> _DayAccumulator:
        day = self.day_of(at)
        accumulator = self._days.get(day)
        if accumulator is None:
            accumulator = _DayAccumulator(day=day)
            self._days[day] = accumulator
        return accumulator

    def _frozen(self, accumulator: _DayAccumulator) -> TradingDayTotals:
        return TradingDayTotals(
            day=accumulator.day,
            realized=accumulator.realized,
            unrealized=accumulator.unrealized,
            realized_r=accumulator.realized_r,
            unrealized_r=accumulator.unrealized_r,
        )


def _validated(value: object, *, field: str) -> Decimal:
    try:
        return require_decimal(value, field=field)
    except RoundingError as exc:
        raise ValueError(str(exc)) from exc
