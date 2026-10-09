"""The ICT silver bullet: two one-hour windows, one setup in each.

Everything here exists to keep one property: **a setup only references what a trader
at that moment could actually see.** The strategy is fed closed bars in time order,
so it cannot see the future by construction. What it *could* do is quote a level that
has not finished forming, and that is the failure this module is built to make
impossible:

* **The London window may only reference the Asian range and the previous day's
  high/low.** The London daily range does not conclude until 05:00 New York, so at
  03:00 it is still being written. The strategy never even receives it: a session
  range is handed to the detectors at the instant it concludes, and the London range
  concludes an hour after the London window has closed.
* **The New York morning window additionally knows the London range**, because by
  10:00 that range has been closed for five hours.

The order lifecycle is the other half. A setup needs a sweep of a permitted level
*and* a market structure shift *and* a fair value gap, all confirmed inside the same
60-minute window. The entry is a limit at the gap's midpoint, the stop sits
``stop_buffer_ticks`` beyond the swept extreme, the target is ``rr_target`` times R,
and the order lives for ``order_expiry_bars`` M5 bars. Open positions are flattened at
16:45 New York whatever else is true.

The strategy never talks to a broker. It returns intents -- place a limit, cancel an
order, flatten a position -- and the runtime or the backtest decides what to do with
them. That is what lets this module be replayed rather than re-implemented.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from zoneinfo import ZoneInfo

from engine.domain.orders import OrderSide
from engine.domain.positions import PositionSide
from engine.market.instrument import InstrumentSpec
from engine.market.models import Bar, Timeframe
from engine.strategy.config import StrategyConfig
from engine.strategy.detectors import (
    FairValueGap,
    Level,
    LiquiditySweep,
    MarketStructureShift,
    StructureDetector,
    StructureSignals,
    SwingKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "ALLOWED_REFERENCES",
    "ASIAN_RANGE_WINDOW",
    "DYNAMIC_CLOSE",
    "LONDON_RANGE_WINDOW",
    "LONDON_WINDOW",
    "NEW_YORK",
    "NY_AM_WINDOW",
    "CancelOrder",
    "FlattenPosition",
    "PlaceLimit",
    "RangeName",
    "SilverBulletStrategy",
    "StrategyFeedError",
    "StrategyIntent",
    "WindowName",
]

#: The pinned zone. Never the host's local time (AGENTS.md 2.6).
NEW_YORK: Final[ZoneInfo] = ZoneInfo("America/New_York")

_ONE_DAY: Final[dt.timedelta] = dt.timedelta(days=1)


class StrategyFeedError(ValueError):
    """The bar stream is not something the strategy can read."""


@dataclasses.dataclass(frozen=True, slots=True)
class LocalWindow:
    """A half-open interval of New York wall-clock time.

    ``start`` is inclusive and ``end`` exclusive, in New York local time. A window
    whose start is after its end wraps midnight, which is what the Asian session
    (20:00 to 00:00) needs. Every comparison happens on the UTC instant, so a DST
    transition moves the window's UTC bounds without moving its wall clock.
    """

    start: dt.time
    end: dt.time

    def __post_init__(self) -> None:
        for name, value in (("start", self.start), ("end", self.end)):
            if not isinstance(value, dt.time):
                raise ValueError(f"{name} must be a time, got {type(value).__name__}")

    @property
    def wraps_midnight(self) -> bool:
        return self.start > self.end

    @property
    def duration(self) -> dt.timedelta:
        day = dt.date(2000, 1, 1)
        span = dt.datetime.combine(day, self.end) - dt.datetime.combine(day, self.start)
        return span if span >= dt.timedelta(0) else span + _ONE_DAY

    def contains_utc(self, at: dt.datetime) -> bool:
        """Whether ``at`` falls inside this window's New York wall clock."""
        if not isinstance(at, dt.datetime) or at.tzinfo is None:
            raise ValueError(f"at must be timezone-aware, got {at!r}")
        local = at.astimezone(NEW_YORK).time()
        if self.wraps_midnight:
            return local >= self.start or local < self.end
        return self.start <= local < self.end

    def start_instant(self, day: dt.date) -> dt.datetime:
        """The UTC instant this window opens on New York date ``day``."""
        return self._instant(day, self.start)

    def end_instant(self, day: dt.date) -> dt.datetime:
        """The UTC instant this window closes on New York date ``day``."""
        return self._instant(day + _ONE_DAY if self.wraps_midnight else day, self.end)

    def _instant(self, day: dt.date, clock: dt.time) -> dt.datetime:
        return dt.datetime.combine(day, clock, tzinfo=NEW_YORK).astimezone(dt.UTC)


class WindowName(StrEnum):
    """The two execution windows."""

    LONDON = "LONDON"
    NY_AM = "NY_AM"


class RangeName(StrEnum):
    """A session range whose extremes the strategy may trade against."""

    ASIAN = "ASIAN"
    LONDON = "LONDON"
    PREVIOUS_DAY = "PREVIOUS_DAY"


#: The London silver bullet: 03:00-04:00 New York, every day of the year.
LONDON_WINDOW: Final[LocalWindow] = LocalWindow(dt.time(3, 0), dt.time(4, 0))

#: The New York morning silver bullet: 10:00-11:00 New York.
NY_AM_WINDOW: Final[LocalWindow] = LocalWindow(dt.time(10, 0), dt.time(11, 0))

#: Every open position is flat by this instant, every day.
DYNAMIC_CLOSE: Final[dt.time] = dt.time(16, 45)

#: The Asian session, which the London window is allowed to reference.
ASIAN_RANGE_WINDOW: Final[LocalWindow] = LocalWindow(dt.time(20, 0), dt.time(0, 0))

#: The London session's daily range, which concludes at 05:00 New York.
LONDON_RANGE_WINDOW: Final[LocalWindow] = LocalWindow(dt.time(0, 0), dt.time(5, 0))

#: What each window is allowed to know. These names are the whole look-ahead policy:
#: a level not listed here is not handed to the detectors while its window is open.
ALLOWED_REFERENCES: Final[dict[WindowName, frozenset[RangeName]]] = {
    # Before 04:00 the London daily range has not concluded, so it is not a level
    # anybody could trade. The Asian range closed at midnight and the previous day's
    # extremes have been fixed since 17:00 the day before.
    WindowName.LONDON: frozenset({RangeName.ASIAN, RangeName.PREVIOUS_DAY}),
    # By 10:00 the London range has been closed for five hours.
    WindowName.NY_AM: frozenset(
        {RangeName.ASIAN, RangeName.LONDON, RangeName.PREVIOUS_DAY}
    ),
}


# --------------------------------------------------------------------------- #
# intents
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True, slots=True)
class PlaceLimit:
    """Rest a limit entry, with its stop and its target already decided.

    Everything a venue needs is here, which is the point: whoever receives this does
    not get to choose a stop, and cannot improve one.
    """

    order_id: str
    side: OrderSide
    limit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: Decimal
    reference: RangeName
    risk: Decimal
    placed_at: dt.datetime
    expires_after_bars: int


@dataclasses.dataclass(frozen=True, slots=True)
class CancelOrder:
    """Withdraw a resting order that will never be filled."""

    order_id: str
    at: dt.datetime
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class FlattenPosition:
    """Close an open position at market, whatever it is worth."""

    position_id: str
    side: OrderSide
    quantity: Decimal
    at: dt.datetime
    reason: str


#: Anything the strategy can ask of a caller.
StrategyIntent = PlaceLimit | CancelOrder | FlattenPosition


@dataclasses.dataclass(frozen=True, slots=True)
class FillReport:
    """What the execution layer tells the strategy about a fill."""

    order_id: str
    position_id: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    timestamp_utc: dt.datetime


@dataclasses.dataclass(frozen=True, slots=True)
class DeclinedSetup:
    """A setup that confirmed but could not be ordered, and why.

    A setup can be refused without anything being wrong: the gap's midpoint can sit
    inside the stop when the reclaim was shallow, and there is no order that
    expresses "risk nothing". Recording it rather than dropping it keeps the reason
    inspectable.
    """

    window: WindowName
    reference: RangeName
    at: dt.datetime
    reason: str


# --------------------------------------------------------------------------- #
# internal state
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(slots=True)
class _SessionBucket:
    """One accumulating session range: its extremes, and the day it belongs to."""

    name: RangeName
    date: dt.date
    high: Decimal | None = None
    low: Decimal | None = None

    def absorb(self, mid_high: Decimal, mid_low: Decimal) -> None:
        self.high = mid_high if self.high is None else max(self.high, mid_high)
        self.low = mid_low if self.low is None else min(self.low, mid_low)

    def conclude_instant(self) -> dt.datetime:
        """The instant this range is finished, as an absolute UTC instant.

        The Asian bucket for date D spans 20:00 on D-1 to 00:00 on D; the London
        bucket for date D spans 00:00 to 05:00 on D; the previous-day bucket spans the
        whole calendar day. Resolved through the zone, so each is wall clock and moves
        with the DST offset in force that morning.
        """
        if self.name is RangeName.ASIAN:
            return _at(self.date, dt.time(0, 0))
        if self.name is RangeName.LONDON:
            return _at(self.date, LONDON_RANGE_WINDOW.end)
        return _at(self.date + _ONE_DAY, dt.time(0, 0))


@dataclasses.dataclass(slots=True)
class _RestingOrder:
    order_id: str
    side: OrderSide
    limit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: Decimal
    reference: RangeName
    risk: Decimal
    placed_index: int
    expires_index: int
    placed_at: dt.datetime


@dataclasses.dataclass(slots=True)
class _Position:
    position_id: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    entry_time: dt.datetime
    stop_price: Decimal
    target_price: Decimal
    reference: RangeName
    risk: Decimal
    closed: bool = False


@dataclasses.dataclass(slots=True)
class _Setup:
    """A sweep waiting for its shift, its gap, or both."""

    window: WindowName
    sweep: LiquiditySweep
    shift: MarketStructureShift | None = None
    gap: FairValueGap | None = None


#: The quantity a strategy defaults to: one troy ounce, the venue's minimum. A
#: production run injects risk-based sizing over the same two prices.
def _default_quantity(entry: Decimal, stop: Decimal) -> Decimal:
    return Decimal(1)


#: The silver bullet trades M5 bars only, and one trading day is 288 of them.
_M5_PER_DAY: Final[int] = 288

#: How long a level stays remembered before it is dropped as unreachable. Two days:
#: a range older than that is refused by the date filter anyway, and an engine that
#: never forgets a level grows without bound.
_LEVEL_HORIZON_BARS: Final[int] = 2 * _M5_PER_DAY


def _at(day: dt.date, clock: dt.time) -> dt.datetime:
    return dt.datetime.combine(day, clock, tzinfo=NEW_YORK).astimezone(dt.UTC)


def _exit_side(side: PositionSide) -> OrderSide:
    """The order side that closes a position."""
    return OrderSide.BUY if side is PositionSide.SHORT else OrderSide.SELL


class SilverBulletStrategy:
    """The silver bullet as a state machine over closed M5 bars.

    Args:
        config: The strategy document.
        spec: The venue's instrument geometry, for the tick the stop is buffered in.
        run_id: Namespaces the order ids the strategy invents.
        quantity_fn: Maps ``(entry, stop)`` to a quantity. Defaults to one unit; a
            production run injects fixed-fractional sizing over the same prices.
    """

    def __init__(
        self,
        *,
        config: StrategyConfig,
        spec: InstrumentSpec,
        run_id: str = "silver-bullet",
        quantity_fn: Callable[[Decimal, Decimal], Decimal] | None = None,
    ) -> None:
        if not isinstance(config, StrategyConfig):
            raise StrategyFeedError(
                f"config must be a StrategyConfig, got {type(config).__name__}"
            )
        if not isinstance(spec, InstrumentSpec):
            raise StrategyFeedError(
                f"spec must be an InstrumentSpec, got {type(spec).__name__}"
            )
        if not isinstance(run_id, str) or not run_id.strip():
            raise StrategyFeedError(f"run_id must be a non-empty string, got {run_id!r}")
        self._config = config
        self._spec = spec
        self._tick = spec.tick
        self._run_id = run_id
        self._quantity_fn = quantity_fn if quantity_fn is not None else _default_quantity
        self._detector = StructureDetector(config=config, tick=self._tick)
        self._levels: list[Level] = []
        self._buckets: list[_SessionBucket] = []
        self._resting: list[_RestingOrder] = []
        self._positions: list[_Position] = []
        self._closed: list[_Position] = []
        self._setup: _Setup | None = None
        self._declined: list[DeclinedSetup] = []
        self._bar_count = -1
        self._order_count = 0
        self._flattened_day: dt.date | None = None
        self._last_timestamp: dt.datetime | None = None

    # -- introspection ------------------------------------------------------- #
    @property
    def config(self) -> StrategyConfig:
        return self._config

    @property
    def bar_count(self) -> int:
        """Closed bars consumed."""
        return self._bar_count + 1

    @property
    def levels(self) -> tuple[Level, ...]:
        """Every session level handed over so far, which is every range concluded."""
        return tuple(self._levels)

    @property
    def resting_orders(self) -> tuple[_RestingOrder, ...]:
        """Limits this strategy has resting and has not withdrawn."""
        return tuple(self._resting)

    @property
    def positions(self) -> tuple[_Position, ...]:
        """Positions this strategy has open."""
        return tuple(position for position in self._positions if not position.closed)

    @property
    def closed_positions(self) -> tuple[_Position, ...]:
        """Positions this strategy has opened and since closed."""
        return tuple(self._closed)

    @property
    def declined(self) -> tuple[DeclinedSetup, ...]:
        """Setups that confirmed but could not be ordered."""
        return tuple(self._declined)

    def window_at(self, at: dt.datetime) -> WindowName | None:
        """The execution window covering ``at``, if any."""
        if LONDON_WINDOW.contains_utc(at):
            return WindowName.LONDON
        if NY_AM_WINDOW.contains_utc(at):
            return WindowName.NY_AM
        return None

    def permitted_levels(self, at: dt.datetime) -> tuple[Level, ...]:
        """The sweep levels the window covering ``at`` is allowed to trade against.

        This is the look-ahead guard expressed as a query, and it has two halves. The
        window decides *which ranges* it may read -- during the London window the
        London daily range is not merely disallowed, it is *absent*, because it had not
        concluded and was never handed over. The date decides *which day's* range it
        may read: yesterday's Asian range has concluded and carries the right name, but
        it is not the level this window trades.
        """
        window = self.window_at(at)
        if window is None:
            return ()
        allowed = {name.value for name in ALLOWED_REFERENCES[window]}
        current = at.astimezone(NEW_YORK).date()
        traded = {current, current - _ONE_DAY}
        return tuple(
            level
            for level in self._levels
            if level.day in traded and level.source in allowed
        )

    # -- the feed ------------------------------------------------------------ #
    def on_bar(self, bar: Bar) -> tuple[StrategyIntent, ...]:
        """Consume one closed M5 bar and return whatever it demands.

        The order inside matters. The bar is folded into the session ranges first, so
        a range that concludes on this bar is available to a setup that confirms on
        this bar; the detector then runs on the same bar the caller has already seen
        closed, so nothing downstream ever reads an unclosed price.
        """
        self._validate(bar)

        self._absorb(bar)
        signals = self._detector.on_bar(bar)

        intents: list[StrategyIntent] = []
        intents.extend(self._expire(bar))
        intents.extend(self._close_the_book(bar))
        intents.extend(self._advance(bar, signals))
        return tuple(intents)

    def on_fill(self, report: FillReport) -> None:
        """Record an entry fill, which opens a position carrying its stop and target."""
        if not isinstance(report, FillReport):
            raise StrategyFeedError(
                f"report must be a FillReport, got {type(report).__name__}"
            )
        for order in list(self._resting):
            if order.order_id != report.order_id:
                continue
            self._resting.remove(order)
            self._positions.append(
                _Position(
                    position_id=report.position_id,
                    side=PositionSide.from_order_side(order.side),
                    quantity=report.quantity,
                    entry_price=report.price,
                    entry_time=report.timestamp_utc,
                    stop_price=order.stop_price,
                    target_price=order.target_price,
                    reference=order.reference,
                    risk=order.risk,
                )
            )
            return
        raise StrategyFeedError(f"fill reported for an unknown order {report.order_id!r}")

    def on_position_closed(self, position_id: str) -> None:
        """Record that a position the strategy opened is now flat."""
        for position in list(self._positions):
            if position.position_id != position_id:
                continue
            position.closed = True
            self._positions.remove(position)
            self._closed.append(position)
            return
        raise StrategyFeedError(f"unknown position {position_id!r}")

    # -- internals ------------------------------------------------------------ #
    def _validate(self, bar: Bar) -> None:
        if not isinstance(bar, Bar):
            raise StrategyFeedError(f"bar must be a Bar, got {type(bar).__name__}")
        if not bar.complete:
            raise StrategyFeedError(
                "the strategy reads closed bars; an incomplete bar has no final close"
            )
        if Timeframe.from_value(bar.timeframe) is not Timeframe.M5:
            raise StrategyFeedError(
                f"the silver bullet trades M5 bars, got {bar.timeframe!r}"
            )
        if self._last_timestamp is not None and bar.timestamp_utc <= self._last_timestamp:
            raise StrategyFeedError(
                "bars must arrive in time order; "
                f"{bar.timestamp_utc.isoformat()} follows {self._last_timestamp.isoformat()}"
            )
        self._last_timestamp = bar.timestamp_utc
        self._bar_count += 1

    def _absorb(self, bar: Bar) -> None:
        """Fold the bar into the ranges it belongs to, and conclude whatever ended.

        A bar can belong to two ranges: at 22:00 it is both the Asian session of the
        next day and part of today's calendar range. A range is only handed to the
        detectors once its own instant has passed, which is what makes a level still
        forming invisible to the sweep scanner.
        """
        mid = bar.mid_ohlc
        at = bar.timestamp_utc
        local_day = at.astimezone(NEW_YORK).date()

        assignments: list[tuple[RangeName, dt.date]] = [(RangeName.PREVIOUS_DAY, local_day)]
        if ASIAN_RANGE_WINDOW.contains_utc(at):
            assignments.insert(0, (RangeName.ASIAN, local_day + _ONE_DAY))
        elif LONDON_RANGE_WINDOW.contains_utc(at):
            assignments.insert(0, (RangeName.LONDON, local_day))

        for name, day in assignments:
            self._bucket_for(name, day).absorb(mid.high, mid.low)

        for bucket in list(self._buckets):
            if bucket.conclude_instant() <= at:
                self._conclude(bucket)
                self._buckets.remove(bucket)

        self._drop_stale_levels()

    def _drop_stale_levels(self) -> None:
        """Forget levels no window that is still open can trade against.

        A range from two days ago is unreachable: the date filter in
        :meth:`permitted_levels` would refuse it anyway, and an engine that never
        forgets a level is an engine that grows without bound.
        """
        horizon = self._bar_count - _LEVEL_HORIZON_BARS
        self._levels = [level for level in self._levels if level.known_at_index >= horizon]
        self._detector.drop_levels(horizon)

    def _bucket_for(self, name: RangeName, day: dt.date) -> _SessionBucket:
        for bucket in self._buckets:
            if bucket.name is name and bucket.date == day:
                return bucket
        bucket = _SessionBucket(name=name, date=day)
        self._buckets.append(bucket)
        return bucket

    def _conclude(self, bucket: _SessionBucket) -> None:
        """Hand a finished range to the detectors as two levels."""
        if bucket.high is None or bucket.low is None:
            return
        concluded_at = bucket.conclude_instant()
        for kind, price in ((SwingKind.HIGH, bucket.high), (SwingKind.LOW, bucket.low)):
            level = Level(
                kind=kind,
                price=price,
                timestamp_utc=concluded_at,
                known_at_index=self._bar_count,
                source=bucket.name.value,
                day=bucket.date,
            )
            self._levels.append(level)
            self._detector.add_level(level)

    def _expire(self, bar: Bar) -> list[StrategyIntent]:
        """Withdraw limits that have outlived ``order_expiry_bars`` bars."""
        intents: list[StrategyIntent] = []
        for order in list(self._resting):
            if self._bar_count > order.expires_index:
                self._resting.remove(order)
                intents.append(
                    CancelOrder(
                        order_id=order.order_id,
                        at=bar.timestamp_utc,
                        reason=f"unfilled after {self._config.order_expiry_bars} M5 bars",
                    )
                )
        return intents

    def _close_the_book(self, bar: Bar) -> list[StrategyIntent]:
        """Flatten the book once the dynamic close instant is reached, once a day."""
        local = bar.timestamp_utc.astimezone(NEW_YORK)
        if local.time() < DYNAMIC_CLOSE:
            return []
        if self._flattened_day == local.date():
            return []
        self._flattened_day = local.date()

        intents: list[StrategyIntent] = []
        for order in list(self._resting):
            self._resting.remove(order)
            intents.append(
                CancelOrder(
                    order_id=order.order_id,
                    at=bar.timestamp_utc,
                    reason="the trading day is closed; a resting entry is an armed entry",
                )
            )
        for position in self.positions:
            intents.append(
                FlattenPosition(
                    position_id=position.position_id,
                    side=_exit_side(position.side),
                    quantity=position.quantity,
                    at=bar.timestamp_utc,
                    reason="dynamic close at 16:45 New York",
                )
            )
        return intents

    def _advance(self, bar: Bar, signals: StructureSignals) -> list[StrategyIntent]:
        """Move the setup forward, and place an order when it completes."""
        at = bar.timestamp_utc
        window = self.window_at(at)

        if self._setup is not None and self._setup.window is not window:
            # The window that opened the setup has closed. An unplaced setup dies with
            # it; a placed order keeps living out its expiry.
            self._setup = None
        if window is None:
            return []

        if self._setup is None:
            if self._has_open_risk():
                return []
            for sweep in signals.sweeps:
                if not self._permitted(sweep, window, at):
                    continue
                self._setup = _Setup(window=window, sweep=sweep)
                break
            return []

        setup = self._setup
        if setup.shift is None:
            setup.shift = self._matching(signals.shifts, setup.sweep.direction, window)
        if setup.gap is None:
            setup.gap = self._matching(signals.gaps, setup.sweep.direction, window)

        if setup.shift is not None and setup.gap is not None:
            return self._place(setup, bar)
        return []

    def _matching(
        self,
        candidates: Sequence[MarketStructureShift | FairValueGap],
        direction: PositionSide,
        window: WindowName,
    ) -> MarketStructureShift | FairValueGap | None:
        """The first candidate in the setup's direction, confirmed inside the window."""
        for candidate in candidates:
            if candidate.direction is not direction:
                continue
            if not self._insides_window(window, candidate.confirmed_at):
                continue
            return candidate
        return None

    def _place(self, setup: _Setup, bar: Bar) -> list[StrategyIntent]:
        """Build the limit order a completed setup asks for."""
        sweep = setup.sweep
        gap = setup.gap
        if gap is None:  # pragma: no cover - the caller has both parts or neither
            return []

        entry = gap.midpoint
        buffer = self._tick * Decimal(self._config.stop_buffer_ticks)
        if sweep.direction is PositionSide.LONG:
            stop = sweep.breach_price - buffer
            risk = entry - stop
            target = entry + (self._config.rr_target * risk)
            side = OrderSide.BUY
        else:
            stop = sweep.breach_price + buffer
            risk = stop - entry
            target = entry - (self._config.rr_target * risk)
            side = OrderSide.SELL

        if risk <= 0:
            return self._decline(setup, bar, "the entry is not beyond the stop")

        quantity = self._quantity_fn(entry, stop)
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, Decimal)
            or not quantity.is_finite()
            or quantity <= 0
        ):
            return self._decline(setup, bar, f"sizing returned {quantity!r}")

        self._order_count += 1
        order_id = f"{self._run_id}-sb-{self._order_count:04d}"
        reference = RangeName(sweep.level.source)
        self._resting.append(
            _RestingOrder(
                order_id=order_id,
                side=side,
                limit_price=entry,
                stop_price=stop,
                target_price=target,
                quantity=quantity,
                reference=reference,
                risk=risk,
                placed_index=self._bar_count,
                expires_index=self._bar_count + self._config.order_expiry_bars,
                placed_at=bar.timestamp_utc,
            )
        )
        self._setup = None
        return [
            PlaceLimit(
                order_id=order_id,
                side=side,
                limit_price=entry,
                stop_price=stop,
                target_price=target,
                quantity=quantity,
                reference=reference,
                risk=risk,
                placed_at=bar.timestamp_utc,
                expires_after_bars=self._config.order_expiry_bars,
            )
        ]

    def _decline(self, setup: _Setup, bar: Bar, reason: str) -> list[StrategyIntent]:
        """Record a setup that could not be ordered, and abandon it."""
        self._declined.append(
            DeclinedSetup(
                window=setup.window,
                reference=RangeName(setup.sweep.level.source),
                at=bar.timestamp_utc,
                reason=reason,
            )
        )
        self._setup = None
        return []

    def _has_open_risk(self) -> bool:
        """Whether a resting order or an open position blocks a fresh setup."""
        return bool(self._resting) or bool(self.positions)

    def _permitted(self, sweep: LiquiditySweep, window: WindowName, at: dt.datetime) -> bool:
        """Whether this sweep is one the window covering ``at`` may act on.

        Three refusals, and none of them is a defect: the sweep predates the window,
        the level's range is not one this window may read, or the level belongs to a
        day this window does not trade. A sweep can be emitted by the detectors and
        still never be traded -- the detector reports what happened, this decides
        what is allowed -- which is why the policy lives here and not there.
        """
        if not self._insides_window(window, sweep.confirmed_at):
            return False
        allowed = {name.value for name in ALLOWED_REFERENCES[window]}
        if sweep.level.source not in allowed:
            return False
        return sweep.level in self.permitted_levels(at)

    def _insides_window(self, window: WindowName, at: dt.datetime) -> bool:
        """Whether a confirmation landed inside the window that opened the setup."""
        if window is WindowName.LONDON:
            return LONDON_WINDOW.contains_utc(at)
        return NY_AM_WINDOW.contains_utc(at)
