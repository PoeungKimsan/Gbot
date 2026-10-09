"""Replay the silver bullet over a bar file, and prove the replay does not repaint.

Two jobs live here, and they are not the same job.

**The replay.** :class:`BacktestRunner` feeds closed bars to the strategy and acts on
its intents through the Phase 2 execution layer, so a backtest is the same code a live
run executes, reading a file instead of a socket. The fill rules are the pessimistic
ones from :mod:`engine.execution.simulator`: entries need a tick of travel, stops
trigger on a touch and fill at the bar extreme beyond the adverse slippage, and a bar
that spans both the entry and the stop is resolved against the trade -- the entry is
assumed filled and immediately stopped out.

**The proof.** :func:`verify_no_repaint` reruns the whole pipeline over every prefix
of a bar list and checks that nothing moves: a signal, an indicator, or a trade that
depended on a bar that had not printed yet would show up as a difference between the
run over ``bars[:n]`` and the full run truncated at bar ``n``. Cross-checking the
streaming indicators against batch recomputations, and the tick-streamed bars against
a batch aggregation of the same quotes, tightens the same screw from the other end.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from engine.domain.orders import OrderSide
from engine.domain.positions import PositionSide
from engine.execution.simulator import Fill, SimulatedBroker
from engine.market.builder import BarAggregator, BarBuilder, BarReconciler, bucket_start
from engine.market.instrument import InstrumentSpec
from engine.market.models import OHLC, Bar, Quote, Timeframe, ohlc_from_prices
from engine.strategy.config import StrategyConfig
from engine.strategy.detectors import StructureDetector, atr_series, swing_series
from engine.strategy.silver_bullet import (
    CancelOrder,
    FillReport,
    FlattenPosition,
    PlaceLimit,
    SilverBulletStrategy,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    #: Maps an entry and its stop to a quantity, as injected by the caller.
    QuantityFn = Callable[[Decimal, Decimal], Decimal]

    from engine.metrics.performance import TradeResult

__all__ = [
    "BacktestOrder",
    "BacktestPosition",
    "BacktestResult",
    "BacktestRunner",
    "BacktestTrade",
    "ExitReason",
    "NoRepaintMismatch",
    "NoRepaintReport",
    "TickStreamReport",
    "batch_m5",
    "replay",
    "stream_m5",
    "verify_no_repaint",
    "verify_tick_stream_matches_batch",
]


class ExitReason(StrEnum):
    """How a position left the book."""

    TARGET = "TARGET"
    STOP = "STOP"
    DYNAMIC_CLOSE = "DYNAMIC_CLOSE"


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    """One round trip, in exact Decimal terms.

    ``r_multiple`` is the trade's result as a multiple of the risk that was taken, so
    it does not depend on the quantity sized. ``pnl`` is the currency result and does.
    """

    trade_id: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    entry_time: dt.datetime
    exit_price: Decimal
    exit_time: dt.datetime
    pnl: Decimal
    r_multiple: Decimal
    reference: str
    exit_reason: ExitReason


@dataclass(frozen=True, slots=True)
class BacktestOrder:
    """An order the strategy asked for, and what became of it."""

    order_id: str
    side: OrderSide
    limit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: Decimal
    reference: str
    placed_at: dt.datetime
    expires_after_bars: int
    status: str
    filled_at: dt.datetime | None = None
    fill_price: Decimal | None = None

    @property
    def geometry(self) -> tuple[object, ...]:
        """The parts of an order that never change once it is placed."""
        return (
            self.order_id,
            self.side,
            self.limit_price,
            self.stop_price,
            self.target_price,
            self.quantity,
            self.reference,
            self.placed_at,
        )


@dataclass(frozen=True, slots=True)
class BacktestPosition:
    """A position still open when the replay stopped."""

    position_id: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    entry_time: dt.datetime
    stop_price: Decimal
    target_price: Decimal
    reference: str


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Everything a replay produced."""

    trades: tuple[BacktestTrade, ...] = ()
    orders: tuple[BacktestOrder, ...] = ()
    open_positions: tuple[BacktestPosition, ...] = ()
    bars: int = 0

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    @property
    def realized_pnl(self) -> Decimal:
        total = Decimal(0)
        for trade in self.trades:
            total += trade.pnl
        return total

    @property
    def expectancy_r(self) -> Decimal | None:
        if not self.trades:
            return None
        total = Decimal(0)
        for trade in self.trades:
            total += trade.r_multiple
        return total / Decimal(len(self.trades))

    @property
    def wins(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for trade in self.trades if trade.pnl < 0)

    def to_trade_results(self) -> tuple[TradeResult, ...]:
        """The trades as :class:`engine.metrics.performance.TradeResult` records.

        Imported lazily so the replay engine does not import the reporting layer at
        runtime: a report is a boundary, and a boundary is what reaches back, not the
        other way round.
        """
        from engine.metrics.performance import TradeResult

        return tuple(
            TradeResult(
                trade_id=trade.trade_id,
                pnl=trade.pnl,
                r_multiple=trade.r_multiple,
                closed_at=trade.exit_time,
            )
            for trade in self.trades
        )

@dataclass(frozen=True, slots=True)
class NoRepaintMismatch:
    """One way in which a replay changed when the future was removed."""

    area: str
    detail: str


@dataclass(frozen=True, slots=True)
class NoRepaintReport:
    """The outcome of rerunning the replay over every prefix."""

    bars: int
    prefixes: int
    mismatches: tuple[NoRepaintMismatch, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.mismatches

    @property
    def reason(self) -> str:
        return self.mismatches[0].detail if self.mismatches else ""


@dataclass(frozen=True, slots=True)
class TickStreamReport:
    """Whether the live aggregation path and the batch path agree."""

    live_bars: int
    batch_bars: int
    bar_discrepancies: tuple[Any, ...] = ()
    indicators_match: bool = True
    trades_match: bool = True

    @property
    def is_clean(self) -> bool:
        return not self.bar_discrepancies and self.indicators_match and self.trades_match


# --------------------------------------------------------------------------- #
# the replay engine
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class _RunningOrder:
    """A resting order and how far it has got."""

    order_id: str
    side: OrderSide
    limit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    quantity: Decimal
    reference: str
    placed_at: dt.datetime
    expires_after_bars: int
    status: str = "RESTING"
    filled_at: dt.datetime | None = None
    fill_price: Decimal | None = None

    def snapshot(self) -> BacktestOrder:
        return BacktestOrder(
            order_id=self.order_id,
            side=self.side,
            limit_price=self.limit_price,
            stop_price=self.stop_price,
            target_price=self.target_price,
            quantity=self.quantity,
            reference=self.reference,
            placed_at=self.placed_at,
            expires_after_bars=self.expires_after_bars,
            status=self.status,
            filled_at=self.filled_at,
            fill_price=self.fill_price,
        )


@dataclass(slots=True)
class _RunningPosition:
    position_id: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    entry_time: dt.datetime
    stop_price: Decimal
    target_price: Decimal
    risk: Decimal
    reference: str
    closed: bool = False

    def snapshot(self) -> BacktestPosition:
        return BacktestPosition(
            position_id=self.position_id,
            side=self.side,
            quantity=self.quantity,
            entry_price=self.entry_price,
            entry_time=self.entry_time,
            stop_price=self.stop_price,
            target_price=self.target_price,
            reference=self.reference,
        )


class BacktestRunner:
    """Replays a bar file through the strategy and the simulated execution layer.

    The order of work inside a bar is the point of the class, and it is deliberately
    not "apply intents, then fill":

    1. Orders resting *before* this bar are evaluated against it, because an order
       placed at this bar's close did not exist while it was trading.
    2. A position opened by step 1 is then tested against the same bar, which is the
       "entry filled and immediately stopped out" case AGENTS.md 2.5 demands.
    3. Only then does the strategy see the closed bar and emit new intents, which take
       effect from the next bar.
    """

    def __init__(
        self,
        *,
        strategy: SilverBulletStrategy,
        spec: InstrumentSpec,
        run_id: str = "backtest",
        broker: SimulatedBroker | None = None,
    ) -> None:
        if not isinstance(strategy, SilverBulletStrategy):
            raise ValueError(
                f"strategy must be a SilverBulletStrategy, got {type(strategy).__name__}"
            )
        if not isinstance(spec, InstrumentSpec):
            raise ValueError(f"spec must be an InstrumentSpec, got {type(spec).__name__}")
        self._strategy = strategy
        self._spec = spec
        self._tick = spec.tick
        self._run_id = run_id
        self._broker = broker if broker is not None else SimulatedBroker(run_id=run_id)
        self._reset()

    def _reset(self) -> None:
        self._resting: dict[str, _RunningOrder] = {}
        self._orders: list[_RunningOrder] = []
        self._positions: list[_RunningPosition] = []
        self._trades: list[BacktestTrade] = []
        self._bars = 0

    @property
    def strategy(self) -> SilverBulletStrategy:
        return self._strategy

    def run(self, bars: Sequence[Bar] | Iterable[Bar]) -> BacktestResult:
        """Replay ``bars``, in order, and return what they produced."""
        self._reset()
        for bar in bars:
            self._bars += 1
            self._fill_resting(bar)
            self._resolve_positions(bar)
            for intent in self._strategy.on_bar(bar):
                self._apply(intent, bar)

        return BacktestResult(
            trades=tuple(self._trades),
            orders=tuple(order.snapshot() for order in self._orders),
            open_positions=tuple(
                position.snapshot() for position in self._positions if not position.closed
            ),
            bars=self._bars,
        )

    # -- internals ------------------------------------------------------------ #
    def _fill_resting(self, bar: Bar) -> None:
        for order_id in list(self._resting):
            order = self._resting[order_id]
            outcome = self._broker.submit_limit(
                bar,
                order.side,
                order.quantity,
                order.limit_price,
                order_id=order.order_id,
                tick=self._tick,
            )
            if not outcome.filled or outcome.fill is None:
                continue
            del self._resting[order_id]
            self._record_fill(order, outcome.fill)

            position = self._open_position(order, outcome.fill)
            self._positions.append(position)
            self._strategy.on_fill(
                FillReport(
                    order_id=order.order_id,
                    position_id=position.position_id,
                    side=order.side,
                    quantity=outcome.fill.quantity,
                    price=outcome.fill.price,
                    timestamp_utc=outcome.fill.filled_at,
                )
            )
            # The bar that filled the entry may also take the stop out: that is the
            # pessimistic reading of an intra-bar touch spanning both (AGENTS.md 2.5).
            self._resolve(position, bar)

    def _resolve_positions(self, bar: Bar) -> None:
        for position in list(self._positions):
            if not position.closed:
                self._resolve(position, bar)

    def _resolve(self, position: _RunningPosition, bar: Bar) -> None:
        """Check one position against one bar, pessimistically."""
        if position.side is PositionSide.LONG:
            # A long stop rests below and triggers on the bid; the target is a limit
            # above, also on the bid.
            stop_hit = bar.bid_ohlc.low <= position.stop_price
            target_hit = bar.bid_ohlc.high >= position.target_price
        else:
            stop_hit = bar.ask_ohlc.high >= position.stop_price
            target_hit = bar.ask_ohlc.low <= position.target_price

        if not (stop_hit or target_hit):
            return

        if stop_hit:
            outcome = self._broker.submit_stop(
                bar,
                _exit_side(position.side),
                position.quantity,
                position.stop_price,
                order_id=f"{position.position_id}-stop",
                tick=self._tick,
            )
            reason = ExitReason.STOP
        else:
            outcome = self._broker.submit_limit(
                bar,
                _exit_side(position.side),
                position.quantity,
                position.target_price,
                order_id=f"{position.position_id}-target",
                tick=self._tick,
            )
            reason = ExitReason.TARGET

        if not outcome.filled or outcome.fill is None:  # pragma: no cover - defensive
            return
        self._close(position, outcome.fill, reason)

    def _open_position(self, order: _RunningOrder, fill: Fill) -> _RunningPosition:
        return _RunningPosition(
            position_id=order.order_id,
            side=PositionSide.from_order_side(order.side),
            quantity=fill.quantity,
            entry_price=fill.price,
            entry_time=fill.filled_at,
            stop_price=order.stop_price,
            target_price=order.target_price,
            risk=abs(fill.price - order.stop_price),
            reference=order.reference,
        )

    def _close(
        self, position: _RunningPosition, fill: Fill, reason: ExitReason
    ) -> None:
        position.closed = True
        self._positions.remove(position)

        per_unit = (fill.price - position.entry_price) * position.side.sign
        self._trades.append(
            BacktestTrade(
                trade_id=position.position_id,
                side=position.side,
                quantity=position.quantity,
                entry_price=position.entry_price,
                entry_time=position.entry_time,
                exit_price=fill.price,
                exit_time=fill.filled_at,
                pnl=per_unit * position.quantity,
                r_multiple=per_unit / position.risk,
                reference=position.reference,
                exit_reason=reason,
            )
        )
        self._strategy.on_position_closed(position.position_id)

    def _apply(self, intent: Any, bar: Bar) -> None:  # noqa: ANN401 - a tagged union
        """Act on one strategy intent, in the order the strategy asked for it."""
        if isinstance(intent, PlaceLimit):
            order = _RunningOrder(
                order_id=intent.order_id,
                side=intent.side,
                limit_price=intent.limit_price,
                stop_price=intent.stop_price,
                target_price=intent.target_price,
                quantity=intent.quantity,
                reference=intent.reference.value,
                placed_at=intent.placed_at,
                expires_after_bars=intent.expires_after_bars,
            )
            if order.order_id not in self._resting:
                self._resting[order.order_id] = order
                self._orders.append(order)
            return

        if isinstance(intent, CancelOrder):
            order = self._resting.pop(intent.order_id, None)
            if order is not None:
                order.status = "CANCELLED"
            return

        if isinstance(intent, FlattenPosition):
            for position in list(self._positions):
                if position.position_id != intent.position_id or position.closed:
                    continue
                outcome = self._broker.submit_market(
                    bar,
                    intent.side,
                    intent.quantity,
                    order_id=f"{intent.position_id}-dynamic-close",
                    tick=self._tick,
                )
                if outcome.filled and outcome.fill is not None:
                    self._close(position, outcome.fill, ExitReason.DYNAMIC_CLOSE)
                break

    def _record_fill(self, order: _RunningOrder, fill: Fill) -> None:
        order.status = "FILLED"
        order.filled_at = fill.filled_at
        order.fill_price = fill.price


def _exit_side(side: PositionSide) -> OrderSide:
    return OrderSide.BUY if side is PositionSide.SHORT else OrderSide.SELL


def replay(
    bars: Sequence[Bar],
    *,
    config: StrategyConfig,
    spec: InstrumentSpec,
    run_id: str = "backtest",
    quantity_fn: QuantityFn | None = None,
) -> BacktestResult:
    """Replay ``bars`` with a fresh strategy, and return what it produced.

    A fresh strategy per call is what makes the prefix comparison in
    :func:`verify_no_repaint` meaningful: each run has no memory of anything outside
    the bars it was given.
    """
    strategy = SilverBulletStrategy(
        config=config, spec=spec, run_id=run_id, quantity_fn=quantity_fn
    )
    return BacktestRunner(strategy=strategy, spec=spec, run_id=run_id).run(bars)


# --------------------------------------------------------------------------- #
# the no-repaint proof
# --------------------------------------------------------------------------- #
def verify_no_repaint(
    bars: Sequence[Bar],
    *,
    config: StrategyConfig,
    spec: InstrumentSpec,
    run_id: str = "no-repaint",
    prefix_stride: int = 1,
    quantity_fn: QuantityFn | None = None,
) -> NoRepaintReport:
    """Check that nothing the replay produced depended on a bar that had not printed.

    Four checks, from the cheapest to the one that would actually bite:

    1. The streaming ATR equals the batch recomputation at every bar.
    2. The streaming swing list equals the batch index scan at the end.
    3. The orders placed while replaying ``bars[:n]`` are the orders the full run
       placed before bar ``n``.
    4. The trades closed while replaying ``bars[:n]`` are the trades the full run
       closed before bar ``n``.

    A repainting signal, a level quoted before it concluded, or a fill that needed a
    future bar would all break one of them.
    """
    if not bars:
        return NoRepaintReport(bars=0, prefixes=0, mismatches=())

    mismatches: list[NoRepaintMismatch] = []
    full = replay(bars, config=config, spec=spec, run_id=run_id, quantity_fn=quantity_fn)

    detector = StructureDetector(config=config, tick=spec.tick)
    streamed_atr: list[Decimal | None] = []
    for bar in bars:
        detector.on_bar(bar)
        streamed_atr.append(detector.atr)
    if streamed_atr != atr_series(bars, window=config.atr_window):
        mismatches.append(
            NoRepaintMismatch(
                "atr", "the streamed ATR disagrees with a batch recomputation"
            )
        )
    if detector.swings != swing_series(bars, swing_n=config.swing_n):
        mismatches.append(
            NoRepaintMismatch(
                "swing", "the streamed swings disagree with a batch scan"
            )
        )

    def run_over_prefix(end: int) -> BacktestResult:
        return replay(
            bars[:end], config=config, spec=spec, run_id=run_id, quantity_fn=quantity_fn
        )

    prefixes = 0
    for end in range(1, len(bars) + 1, prefix_stride):
        boundary = bars[end - 1].timestamp_utc
        prefix = run_over_prefix(end)
        prefixes += 1

        expected_orders = tuple(
            order for order in full.orders if order.placed_at <= boundary
        )
        if tuple(order.geometry for order in prefix.orders) != tuple(
            order.geometry for order in expected_orders
        ):
            mismatches.append(
                NoRepaintMismatch(
                    "orders",
                    f"the orders placed through bar {end - 1} differ from the full run",
                )
            )

        expected_trades = tuple(
            trade for trade in full.trades if trade.exit_time <= boundary
        )
        if prefix.trades != expected_trades:
            mismatches.append(
                NoRepaintMismatch(
                    "trades",
                    f"the trades closed through bar {end - 1} differ from the full run",
                )
            )

    return NoRepaintReport(bars=len(bars), prefixes=prefixes, mismatches=tuple(mismatches))


# --------------------------------------------------------------------------- #
# tick-streamed vs batch aggregation
# --------------------------------------------------------------------------- #
def stream_m5(quotes: Iterable[Quote], *, spec: InstrumentSpec) -> list[Bar]:
    """The live path: fold a tick stream into M5 bars through the Phase 1 aggregators.

    A bar appears only when the bucket after it has started, so the tail of the list
    is a bar that has not closed yet and is not published. That is the honest live
    behaviour, and it is why a batch fold and a stream agree on every bar the stream
    has actually closed and not on the last one.
    """
    builder = BarBuilder(spec=spec, timeframe=Timeframe.M1)
    aggregator = BarAggregator(spec=spec, source=Timeframe.M1, target=Timeframe.M5)
    bars: list[Bar] = []
    for quote in quotes:
        for minute in builder.add_quote(quote):
            bars.extend(aggregator.add(minute))
    return bars


def batch_m5(quotes: Iterable[Quote], *, spec: InstrumentSpec) -> list[Bar]:
    """The batch path: fold the whole quote list into M5 bars directly.

    Written as bucket comprehensions rather than as a state machine, so it is a
    genuinely different implementation from :func:`stream_m5`. Two folds, in bulk:
    quotes into M1 buckets, then M1 bars into M5 buckets.
    """
    minute: dict[dt.datetime, list[Quote]] = {}
    for quote in quotes:
        minute.setdefault(bucket_start(quote.timestamp_utc, Timeframe.M1), []).append(quote)
    minutes = [_minute_bar(started, minute[started], spec=spec) for started in sorted(minute)]

    five: dict[dt.datetime, list[Bar]] = {}
    for bar in minutes:
        five.setdefault(bucket_start(bar.timestamp_utc, Timeframe.M5), []).append(bar)
    return [_five_minute_bar(started, five[started]) for started in sorted(five)]


def _minute_bar(started: dt.datetime, quotes: list[Quote], *, spec: InstrumentSpec) -> Bar:
    return Bar(
        timeframe="M1",
        timestamp_utc=started,
        complete=True,
        volume=len(quotes),
        bid_ohlc=ohlc_from_prices(quote.bid for quote in quotes),
        ask_ohlc=ohlc_from_prices(quote.ask for quote in quotes),
    )


def _five_minute_bar(started: dt.datetime, bars: list[Bar]) -> Bar:
    materialized = list(bars)
    return Bar(
        timeframe="M5",
        timestamp_utc=started,
        complete=True,
        volume=sum(bar.volume for bar in materialized),
        bid_ohlc=_fold(bar.bid_ohlc for bar in materialized),
        ask_ohlc=_fold(bar.ask_ohlc for bar in materialized),
    )


def _fold(ohlcs: Iterable[OHLC]) -> OHLC:
    materialized = list(ohlcs)
    return OHLC(
        open=materialized[0].open,
        high=max(bar.high for bar in materialized),
        low=min(bar.low for bar in materialized),
        close=materialized[-1].close,
    )


def verify_tick_stream_matches_batch(
    quotes: Iterable[Quote],
    *,
    config: StrategyConfig,
    spec: InstrumentSpec,
    run_id: str = "tick-stream",
    quantity_fn: QuantityFn | None = None,
) -> TickStreamReport:
    """Prove that a bar streamed from ticks is the bar a batch fold produces.

    The same quote list is aggregated twice -- once incrementally through
    :class:`~engine.market.builder.BarBuilder` and
    :class:`~engine.market.builder.BarAggregator`, once in bulk -- and the resulting
    bars are reconciled through the Phase 1 reconciler. The strategy is then replayed
    over both, because a bar that merely *looks* the same while producing different
    signals would make every other guarantee here decorative.

    The streaming path closes a bar only once the bucket after it has started, so the
    comparison covers every bar the stream has actually closed rather than the last
    one it was still accumulating.
    """
    quotes = list(quotes)
    if not quotes:
        return TickStreamReport(live_bars=0, batch_bars=0)

    live = stream_m5(quotes, spec=spec)
    batch = batch_m5(quotes, spec=spec)
    streamed_starts = {bucket_start(bar.timestamp_utc, Timeframe.M5) for bar in live}
    comparable = [
        bar for bar in batch if bucket_start(bar.timestamp_utc, Timeframe.M5) in streamed_starts
    ]

    reconciler = BarReconciler(spec=spec, timeframe=Timeframe.M5)
    discrepancies = tuple(reconciler.reconcile(live, comparable))

    markers = atr_series(live, window=config.atr_window) == atr_series(
        comparable, window=config.atr_window
    )
    markers = markers and swing_series(live, swing_n=config.swing_n) == swing_series(
        comparable, swing_n=config.swing_n
    )

    trades_match = (
        replay(live, config=config, spec=spec, run_id=run_id, quantity_fn=quantity_fn).trades
        == replay(
            comparable, config=config, spec=spec, run_id=run_id, quantity_fn=quantity_fn
        ).trades
    )

    return TickStreamReport(
        live_bars=len(live),
        batch_bars=len(batch),
        bar_discrepancies=discrepancies,
        indicators_match=markers,
        trades_match=trades_match,
    )
