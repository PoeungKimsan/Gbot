"""Closed-bar market structure: swings, sweeps, shifts, and imbalances.

Four detectors, in the order a silver-bullet setup discovers them, and one rule that
governs all four: **a signal is computed from closed mid bars and nothing else.** An
incomplete bar has no final close, so a signal computed from one would change as the
bar develops, and a backtest replayed against those values never actually happened.
The batch functions (:func:`swing_series`, :func:`atr_series`) and the streaming
engine (:class:`StructureDetector`) are deliberately two implementations of the same
definitions, because that is what lets the replay prove they agree bar by bar.

Beyond the closed-bar rule, two orderings are load-bearing:

* **A swing point exists ``swing_n`` bars after it prints.** Fractals need their full
  right shoulder, so the newest ``swing_n`` bars can never be a swing. A swing is
  attributed to the bar that formed it and *confirmed* by the bar that completed it.
* **A level exists only once it is knowable.** A fractal swing becomes tradable from
  its confirmation bar; a session range becomes tradable at the instant it concludes,
  which is supplied by the caller through :class:`Level`. The London daily range is
  the reason this rule is spelled out: it does not conclude until 05:00 New York, so a
  03:00 bar literally cannot reference it, because the level has not been handed to the
  detector yet.

Signals are reported once, at the bar that confirms them, and never restated.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from engine.domain.positions import PositionSide
from engine.market.models import OHLC, Bar
from engine.strategy.config import StrategyConfig

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "FRACTAL_SOURCE",
    "FairValueGap",
    "Level",
    "LiquiditySweep",
    "MarketStructureShift",
    "StructureDetector",
    "StructureSignals",
    "SwingKind",
    "SwingPoint",
    "atr_series",
    "swing_series",
]

#: The source a fractal swing carries. A session-range level carries the name of the
#: range it came from instead, which is what lets the silver bullet's reference guard
#: tell them apart without importing anything from it.
FRACTAL_SOURCE: Final[str] = "FRACTAL"


class SwingKind(StrEnum):
    """Which extreme a swing point or a level marks."""

    HIGH = "HIGH"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class SwingPoint:
    """A fractal extreme, and the bar that made it public knowledge.

    ``index`` is the bar that printed the extreme; ``confirmed_index`` is
    ``index + swing_n``, the first bar whose arrival had the full right shoulder.
    Anything that reads a swing before its confirmation is reading the future.
    """

    kind: SwingKind
    price: Decimal
    timestamp_utc: dt.datetime
    index: int
    confirmed_index: int
    confirmed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class Level:
    """An extreme a sweep may pierce.

    ``known_at_index`` is the bar index from which the level is public. For a fractal
    swing that is its confirmation index; for a session range it is the bar that
    carries the range's conclusion, supplied by the caller that computed it.

    ``day`` is the New York date the level belongs to, or ``None`` for a fractal. The
    caller that owns the session calendar decides which day a level may still be
    traded on, so the date travels with the level rather than being inferred from it:
    yesterday's Asian range is not a level today's London window may trade.
    """

    kind: SwingKind
    price: Decimal
    timestamp_utc: dt.datetime
    known_at_index: int
    source: str = FRACTAL_SOURCE
    day: dt.date | None = None


@dataclass(frozen=True, slots=True)
class LiquiditySweep:
    """A level pierced by at least ``sweep_min_ticks``, then reclaimed.

    ``direction`` is the side of the setup the sweep argues *for*: a level swept to
    the downside is a long, because the stops underneath it have now been taken.
    """

    direction: PositionSide
    level: Level
    breach_price: Decimal
    breach_at: dt.datetime
    close_back_price: Decimal
    confirmed_at: dt.datetime
    confirmed_index: int

    @property
    def depth(self) -> Decimal:
        """How far the price travelled through the level, in price units."""
        if self.level.kind is SwingKind.HIGH:
            return self.breach_price - self.level.price
        return self.level.price - self.breach_price


@dataclass(frozen=True, slots=True)
class MarketStructureShift:
    """A close through the most recent swing point, on a body worth reading."""

    direction: PositionSide
    broken: SwingPoint
    close_price: Decimal
    body: Decimal
    atr: Decimal
    confirmed_at: dt.datetime
    confirmed_index: int


@dataclass(frozen=True, slots=True)
class FairValueGap:
    """A three-bar imbalance, and the midpoint its limit entry rests at.

    ``midpoint`` is quantized to the tick in the direction that hurts the trader: a
    buyer's entry rounds up, a seller's rounds down. A gap is priced in ticks, so a
    half-tick midpoint is not a price anybody can be filled at.
    """

    direction: PositionSide
    top: Decimal
    bottom: Decimal
    midpoint: Decimal
    size: Decimal
    confirmed_at: dt.datetime
    confirmed_index: int


@dataclass(frozen=True, slots=True)
class StructureSignals:
    """Everything that became true on one closed bar."""

    swings: tuple[SwingPoint, ...] = ()
    sweeps: tuple[LiquiditySweep, ...] = ()
    shifts: tuple[MarketStructureShift, ...] = ()
    gaps: tuple[FairValueGap, ...] = ()


@dataclass(frozen=True, slots=True)
class _MidBar:
    """The closed mid side of one bar, flattened for arithmetic."""

    timestamp_utc: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    @classmethod
    def of(cls, bar: Bar) -> _MidBar:
        mid = bar.mid_ohlc
        return cls(
            timestamp_utc=bar.timestamp_utc,
            open=mid.open,
            high=mid.high,
            low=mid.low,
            close=mid.close,
        )

    @property
    def body(self) -> Decimal:
        return abs(self.close - self.open)


@dataclass(slots=True)
class _LevelState:
    """Sweep-scan state for one level: what has been pierced, and whether it still can."""

    level: Level
    swept: bool = False
    breach_index: int | None = None
    deadline_index: int | None = None


# --------------------------------------------------------------------------- #
# ATR
# --------------------------------------------------------------------------- #
class ATR:
    """Rolling average true range over the last ``window`` closed bars.

    Streaming counterpart to :func:`atr_series`. It keeps a running total rather
    than re-summing, and reports ``None`` until the window is full: a mean of three
    bars is not an average true range, and a threshold computed from one would be a
    threshold nobody calibrated.
    """

    def __init__(self, *, window: int) -> None:
        _require_window("window", window)
        self._window = window
        self._ranges: deque[Decimal] = deque()
        self._total: Decimal = Decimal(0)
        self._previous_close: Decimal | None = None

    @property
    def value(self) -> Decimal | None:
        """The mean true range, or ``None`` while the window is still filling."""
        if len(self._ranges) < self._window:
            return None
        return self._total / Decimal(len(self._ranges))

    def update(self, bar: Bar) -> Decimal | None:
        """Record one closed bar. An incomplete bar leaves the running value alone."""
        if not isinstance(bar, Bar):
            raise TypeError(f"bar must be a Bar, got {type(bar).__name__}")
        if not bar.complete:
            return self.value

        mid = bar.mid_ohlc
        true_range = _true_range(mid, self._previous_close)
        self._total += true_range
        self._ranges.append(true_range)
        if len(self._ranges) > self._window:
            self._total -= self._ranges.popleft()
        self._previous_close = mid.close
        return self.value


def atr_series(bars: Sequence[Bar], *, window: int) -> list[Decimal | None]:
    """The ATR after every bar, recomputed from scratch at each step.

    Deliberately not the streaming algorithm: re-deriving each value from the window
    is what makes :class:`ATR` a checked implementation rather than a trusted one.

    Args:
        bars: Closed bars, in order. Incomplete bars contribute nothing and leave the
            running value where it was.
        window: ATR window in bars.

    Returns:
        One entry per input bar: the ATR at that point, or ``None`` while the window
        is still filling.
    """
    _require_window("window", window)

    values: list[Decimal | None] = []
    ranges: deque[Decimal] = deque()
    previous_close: Decimal | None = None

    for bar in bars:
        if not isinstance(bar, Bar):
            raise TypeError(f"bars must be Bars, got {type(bar).__name__}")
        if bar.complete:
            mid = bar.mid_ohlc
            ranges.append(_true_range(mid, previous_close))
            if len(ranges) > window:
                ranges.popleft()
            previous_close = mid.close
        values.append(
            sum(ranges) / Decimal(len(ranges)) if len(ranges) >= window else None
        )
    return values


def _true_range(mid: OHLC, previous_close: Decimal | None) -> Decimal:
    """``max(high-low, |high-prev|, |low-prev|)``, so a gap away registers."""
    span = mid.high - mid.low
    if previous_close is None:
        return span
    return max(span, abs(mid.high - previous_close), abs(mid.low - previous_close))


# --------------------------------------------------------------------------- #
# batch detectors
# --------------------------------------------------------------------------- #
def swing_series(bars: Sequence[Bar], *, swing_n: int) -> tuple[SwingPoint, ...]:
    """Every fractal swing in ``bars``, in the order the stream would confirm them.

    Args:
        bars: Closed bars, in order.
        swing_n: Bars required on each side, all strictly less extreme.

    Returns:
        The swings. A bar with fewer than ``swing_n`` bars to its right is not a swing,
        so a swing known at prefix ``n`` is never added to by extending the prefix.
    """
    for bar in bars:
        if not isinstance(bar, Bar):
            raise TypeError(f"bars must be Bars, got {type(bar).__name__}")
    _require_positive_count("swing_n", swing_n)

    mids = [bar.mid_ohlc for bar in bars]
    found: list[SwingPoint] = []
    for index in range(swing_n, len(bars) - swing_n):
        for kind in (SwingKind.HIGH, SwingKind.LOW):
            if _is_fractal(mids, index, kind, swing_n):
                found.append(
                    SwingPoint(
                        kind=kind,
                        price=mids[index].high if kind is SwingKind.HIGH else mids[index].low,
                        timestamp_utc=bars[index].timestamp_utc,
                        index=index,
                        confirmed_index=index + swing_n,
                        confirmed_at=bars[index + swing_n].timestamp_utc,
                    )
                )
    return tuple(found)


def _is_fractal(
    mids: Sequence[OHLC | _MidBar], index: int, kind: SwingKind, swing_n: int
) -> bool:
    """Whether ``mids[index]`` is strictly the extreme of its ``swing_n``-wide neighbourhood.

    A neighbour offset outside the sequence means the shoulder does not exist, and a
    bar without both shoulders is not a fractal: Python would otherwise wrap a negative
    index back onto the far end of the list and manufacture a swing out of nothing.
    """
    value = _extreme(mids[index], kind)
    for neighbour in range(index - swing_n, index + swing_n + 1):
        if neighbour == index:
            continue
        if neighbour < 0 or neighbour >= len(mids):
            return False
        other = _extreme(mids[neighbour], kind)
        if kind is SwingKind.HIGH and other >= value:
            return False
        if kind is SwingKind.LOW and other <= value:
            return False
    return True


def _extreme(bar: OHLC | _MidBar, kind: SwingKind) -> Decimal:
    return bar.high if kind is SwingKind.HIGH else bar.low


# --------------------------------------------------------------------------- #
# the streaming engine
# --------------------------------------------------------------------------- #
class StructureDetector:
    """Causal detector engine: closed bars in, signals out.

    The engine is the live path. It holds the closed bars it has been given, the swings
    they produced, and the sweep targets a caller has handed it -- nothing else, and
    nothing from outside them. A signal for bar *n* is therefore a function of bars
    ``0..n`` alone, which is the property :mod:`engine.strategy.backtest` verifies by
    rerunning the engine over every prefix of a bar file and checking nothing moves.

    The caller owns level lifetime: :meth:`add_level` is called when a level becomes
    knowable, and :meth:`drop_levels` when it has become unreachable.
    """

    def __init__(self, *, config: StrategyConfig, tick: Decimal) -> None:
        if not isinstance(config, StrategyConfig):
            raise TypeError(f"config must be a StrategyConfig, got {type(config).__name__}")
        _require_tick(tick)
        self._config = config
        self._tick = tick
        self._atr = ATR(window=config.atr_window)
        self._bars: list[_MidBar] = []
        self._swings: list[SwingPoint] = []
        self._latest: dict[SwingKind, SwingPoint] = {}
        self._levels: list[_LevelState] = []
        self._bar_count = 0

    # -- introspection ------------------------------------------------------- #
    @property
    def bar_count(self) -> int:
        """Closed bars consumed."""
        return self._bar_count

    @property
    def atr(self) -> Decimal | None:
        """The ATR in force, or ``None`` while the window is still filling."""
        return self._atr.value

    @property
    def swings(self) -> tuple[SwingPoint, ...]:
        """Every swing confirmed so far, in confirmation order."""
        return tuple(self._swings)

    # -- input --------------------------------------------------------------- #
    def add_level(self, level: Level) -> None:
        """Offer a sweep target that has just become knowable.

        The caller owns the timing: a session range is handed over at the instant it
        concludes, so a bar inside the range cannot sweep it.
        """
        if not isinstance(level, Level):
            raise TypeError(f"level must be a Level, got {type(level).__name__}")
        self._levels.append(_LevelState(level=level))

    def drop_levels(self, before_index: int) -> int:
        """Forget every level that became public before ``before_index``.

        Memory management for a process that runs for weeks: a level that old cannot
        be swept by a window that is still open, and the caller is the only thing that
        knows what "old enough" means. Returns how many levels were dropped.
        """
        if isinstance(before_index, bool) or not isinstance(before_index, int):
            raise TypeError(f"before_index must be an int, got {type(before_index).__name__}")
        retained = [state for state in self._levels if state.level.known_at_index >= before_index]
        dropped = len(self._levels) - len(retained)
        self._levels.clear()
        self._levels.extend(retained)
        return dropped

    def on_bar(self, bar: Bar) -> StructureSignals:
        """Consume one bar, returning everything it confirmed.

        An incomplete bar is ignored: it has no final close, so nothing computed from
        it is a fact.
        """
        if not isinstance(bar, Bar):
            raise TypeError(f"bar must be a Bar, got {type(bar).__name__}")
        if not bar.complete:
            return StructureSignals()

        self._bars.append(_MidBar.of(bar))
        index = self._bar_count
        self._bar_count += 1
        atr = self._atr.update(bar)

        swings = self._confirm_swings(index)
        for swing in swings:
            self._swings.append(swing)
            self._latest[swing.kind] = swing
            # A fractal is a level of its own, tradable from its confirmation.
            self._levels.append(
                _LevelState(
                    level=Level(
                        kind=swing.kind,
                        price=swing.price,
                        timestamp_utc=swing.timestamp_utc,
                        known_at_index=swing.confirmed_index,
                        source=FRACTAL_SOURCE,
                    )
                )
            )

        return StructureSignals(
            swings=tuple(swings),
            sweeps=tuple(self._scan_sweeps(index)),
            shifts=tuple(self._scan_shifts(index, atr)),
            gaps=tuple(self._scan_gaps(index, atr)),
        )

    # -- internals ------------------------------------------------------------ #
    def _confirm_swings(self, index: int) -> list[SwingPoint]:
        """The swing, if any, whose right shoulder this bar just completed."""
        candidate = index - self._config.swing_n
        if candidate < 0:
            return []

        confirmed_at = self._bars[index].timestamp_utc
        found: list[SwingPoint] = []
        for kind in (SwingKind.HIGH, SwingKind.LOW):
            if not _is_fractal(self._bars, candidate, kind, self._config.swing_n):
                continue
            current = self._bars[candidate]
            found.append(
                SwingPoint(
                    kind=kind,
                    price=current.high if kind is SwingKind.HIGH else current.low,
                    timestamp_utc=current.timestamp_utc,
                    index=candidate,
                    confirmed_index=index,
                    confirmed_at=confirmed_at,
                )
            )
        return found

    def _scan_sweeps(self, index: int) -> list[LiquiditySweep]:
        """Pierce levels that are public, and reclaim them inside the window."""
        current = self._bars[index]
        emitted: list[LiquiditySweep] = []
        for state in self._levels:
            if state.swept:
                continue
            level = state.level
            if level.known_at_index > index:
                # Not knowable yet: a session range still concluding is invisible.
                continue

            if state.breach_index is None:
                if not _breaches(current, level, self._tick, self._config.sweep_min_ticks):
                    continue
                state.breach_index = index
                state.deadline_index = index + self._config.sweep_close_back_bars - 1

            if _closes_back(current, level):
                state.swept = True
                breaching = self._bars[state.breach_index]
                emitted.append(
                    LiquiditySweep(
                        direction=_setup_side(level.kind),
                        level=level,
                        breach_price=(
                            breaching.high if level.kind is SwingKind.HIGH else breaching.low
                        ),
                        breach_at=breaching.timestamp_utc,
                        close_back_price=current.close,
                        confirmed_at=current.timestamp_utc,
                        confirmed_index=index,
                    )
                )
            elif state.deadline_index is not None and index >= state.deadline_index:
                # Broken and left broken: a breakout, not a sweep. The level stays
                # available for a later attempt.
                state.breach_index = None
                state.deadline_index = None
        return emitted

    def _scan_shifts(self, index: int, atr: Decimal | None) -> list[MarketStructureShift]:
        """A close through the most recent swing of each kind, on a real body."""
        if atr is None:
            return []

        current = self._bars[index]
        threshold = self._config.mss_body_k * atr
        body = current.body
        if body < threshold:
            return []

        emitted: list[MarketStructureShift] = []
        for kind, direction in (
            (SwingKind.HIGH, PositionSide.LONG),
            (SwingKind.LOW, PositionSide.SHORT),
        ):
            latest = self._latest.get(kind)
            if latest is None:
                continue
            broken = (
                current.close > latest.price
                if kind is SwingKind.HIGH
                else current.close < latest.price
            )
            if not broken:
                continue
            emitted.append(
                MarketStructureShift(
                    direction=direction,
                    broken=latest,
                    close_price=current.close,
                    body=body,
                    atr=atr,
                    confirmed_at=current.timestamp_utc,
                    confirmed_index=index,
                )
            )
        return emitted

    def _scan_gaps(self, index: int, atr: Decimal | None) -> list[FairValueGap]:
        """A three-bar imbalance on this bar, in either direction."""
        if atr is None or index < 2:
            return []

        current = self._bars[index]
        outer = self._bars[index - 2]
        threshold = self._config.fvg_min_atr * atr
        emitted: list[FairValueGap] = []

        bullish = current.low - outer.high
        if bullish > 0 and bullish >= threshold:
            emitted.append(
                _gap(
                    direction=PositionSide.LONG,
                    top=current.low,
                    bottom=outer.high,
                    size=bullish,
                    tick=self._tick,
                    confirmed_at=current.timestamp_utc,
                    confirmed_index=index,
                )
            )
        bearish = outer.low - current.high
        if bearish > 0 and bearish >= threshold:
            emitted.append(
                _gap(
                    direction=PositionSide.SHORT,
                    top=outer.low,
                    bottom=current.high,
                    size=bearish,
                    tick=self._tick,
                    confirmed_at=current.timestamp_utc,
                    confirmed_index=index,
                )
            )
        return emitted


def _gap(
    *,
    direction: PositionSide,
    top: Decimal,
    bottom: Decimal,
    size: Decimal,
    tick: Decimal,
    confirmed_at: dt.datetime,
    confirmed_index: int,
) -> FairValueGap:
    return FairValueGap(
        direction=direction,
        top=top,
        bottom=bottom,
        midpoint=_midpoint(top, bottom, direction, tick),
        size=size,
        confirmed_at=confirmed_at,
        confirmed_index=confirmed_index,
    )


def _midpoint(top: Decimal, bottom: Decimal, direction: PositionSide, tick: Decimal) -> Decimal:
    """The gap's midpoint, rounded to the tick against the trader.

    A buyer's limit rounds up and a seller's rounds down, so the entry is never
    improved by the rounding of an odd midpoint.
    """
    middle = (top + bottom) / Decimal(2)
    step = Decimal(1).scaleb(tick.as_tuple().exponent)
    mode = ROUND_UP if direction is PositionSide.LONG else ROUND_DOWN
    return middle.quantize(step, rounding=mode)


def _setup_side(kind: SwingKind) -> PositionSide:
    """A swept high is a short; a swept low is a long."""
    return PositionSide.SHORT if kind is SwingKind.HIGH else PositionSide.LONG


def _breaches(current: _MidBar, level: Level, tick: Decimal, min_ticks: int) -> bool:
    """Whether this bar traded at least ``min_ticks`` through the level."""
    travel = tick * Decimal(min_ticks)
    if level.kind is SwingKind.HIGH:
        return current.high >= level.price + travel
    return current.low <= level.price - travel


def _closes_back(current: _MidBar, level: Level) -> bool:
    """Whether this bar's close is back inside the level it pierced."""
    if level.kind is SwingKind.HIGH:
        return current.close <= level.price
    return current.close >= level.price


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _require_window(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive int, got {value!r}")


def _require_positive_count(field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive int, got {value!r}")


def _require_tick(tick: object) -> None:
    if isinstance(tick, bool) or not isinstance(tick, Decimal) or not tick.is_finite() or tick <= 0:
        raise ValueError(f"tick must be a positive finite Decimal, got {tick!r}")
