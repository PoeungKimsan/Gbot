"""Deterministic, pessimistic fill simulation.

Every rule here is chosen so a backtest reports a *worse* result than reality, never a
better one. That direction is the whole design goal: a simulator that is optimistic in any
one of these places will show a strategy as profitable when it was not.

The rules, and the trap each one avoids:

* **Buys on the ask, sells on the bid.** Filling on mid hides the spread, which for gold is
  real money.
* **Long stop on the bid, short stop on the ask.** A long stop-loss rests below the market,
  and what prints below is the bid; using the ask would trigger late and book a smaller loss
  than the trade actually took.
* **Limits need a full tick of travel.** A bar whose low *touches* a buy limit has not
  necessarily filled it -- a touch at the level with no trade through is a limit order
  sitting unfilled. Requiring one tick ($0.01) of overshoot is the pessimistic reading.
* **Market and stop fills take the bar extreme.** A market buy pays the highest ask in the
  bar; a sell stop receives the lowest bid. Both are the worst price the trade could have
  seen, which is what makes them usable.
* **Intra-bar ambiguity resolves against the trade.** If a bar spans both the limit entry
  and the stop, the entry is assumed filled and immediately stopped. Assuming the other
  order would let a backtest keep the favourable half of the bar.

Slippage is *seeded* rather than random: the seed is derived from ``run_id`` and
``order_id`` through SHA-256, so a given replay is byte-for-byte reproducible while still
being adverse and hard to reverse-engineer. Integer ticks, not floats, so the arithmetic
stays exact.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import random
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    pass

__all__ = [
    "Fill",
    "FillKind",
    "OrderOutcome",
    "SeededSlippage",
    "SimulatedBroker",
    "derive_seed",
    "entry_price_for",
    "is_price_past_level",
    "slippage_seed",
]


def derive_seed(run_id: str, order_id: str) -> int:
    """Deterministic seed for one order in one run.

    SHA-256 over the first eight bytes, as an integer. Deterministic so a replay of the same
    run reproduces the same fills, and order-specific so two orders in the same run do not
    share a slippage stream.
    """
    for name, value in (("run_id", run_id), ("order_id", order_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string, got {value!r}")
    digest = hashlib.sha256(f"{run_id}:{order_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


#: Alias kept because "seed" reads better at the call site than "derive".
slippage_seed = derive_seed


class FillKind(StrEnum):
    """What produced a fill."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LOSS = "STOP_LOSS"


@dataclass(frozen=True, slots=True)
class Fill:
    """One fill, with the price it would have really paid rather than the ideal one.

    ``reference_price`` is what the bar offered before slippage; ``price`` is what the
    simulator actually used. Keeping both makes the slippage auditable instead of hidden.
    """

    order_id: str
    run_id: str
    side: Any
    kind: FillKind
    quantity: Decimal
    reference_price: Decimal
    price: Decimal
    slippage_ticks: int
    slippage: Decimal
    seed: int
    filled_at: dt.datetime
    bar_start: dt.datetime


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """Whether an order filled, and at what price."""

    filled: bool
    fill: Fill | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class IntraBarOutcome:
    """Result of evaluating one bar against an entry/stop pair.

    ``stopped_out`` is only ever true when ``entry_filled`` is: an order that never filled
    cannot have been stopped, and a simulator that reports otherwise has invented a trade.
    """

    entry_filled: bool
    stopped_out: bool
    entry_time: dt.datetime | None = None
    exit_time: dt.datetime | None = None
    realized: Decimal = Decimal(0)
    entry_fill: Fill | None = None
    exit_fill: Fill | None = None


class SeededSlippage:
    """Adverse slippage, deterministic per order.

    Each order draws its own ``random.Random(seed)`` instance, so slippage for one order
    cannot shift another's. Draws are integer tick counts, so the resulting price is exact.
    """

    def __init__(self, *, run_id: str, max_ticks: int = 8) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError(f"run_id must be a non-empty string, got {run_id!r}")
        if isinstance(max_ticks, bool) or not isinstance(max_ticks, int) or max_ticks < 0:
            raise ValueError(f"max_ticks must be a non-negative int, got {max_ticks!r}")
        self._run_id = run_id
        self._max_ticks = max_ticks
        self._cache: dict[str, int] = {}

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def max_ticks(self) -> int:
        return self._max_ticks

    def _ticks_for(self, order_id: str) -> int:
        if order_id in self._cache:
            return self._cache[order_id]
        rng = random.Random(derive_seed(self._run_id, order_id))  # noqa: S311 - seeded, not security
        # randrange is integer-valued, so no float ever enters the price.
        ticks = rng.randrange(0, self._max_ticks + 1)
        self._cache[order_id] = ticks
        return ticks

    def for_order(self, order_id: str) -> int:
        """Slippage in integer ticks for this order."""
        return self._ticks_for(order_id)

    def apply(self, *, side: Any, reference: Decimal, order_id: str) -> Decimal:
        """Apply adverse slippage to a reference price.

        A buy always pays more and a sell always receives less, regardless of which side the
        order is *closing*: slippage is the cost of crossing the spread, and it always costs
        the trader.
        """
        if not isinstance(reference, Decimal):
            raise ValueError(f"reference must be a Decimal, got {type(reference).__name__}")
        ticks = self._ticks_for(order_id)
        if ticks == 0:
            return reference
        direction = self._direction(side)
        return reference + (Decimal(ticks) * Decimal("0.01") * direction)

    @staticmethod
    def _direction(side: Any) -> Decimal:
        from engine.domain.orders import OrderSide

        if side is OrderSide.BUY:
            return Decimal(1)
        if side is OrderSide.SELL:
            return Decimal(-1)
        raise ValueError(f"side must be BUY or SELL, got {side!r}")


def is_price_past_level(price: Decimal, level: Decimal, tick: Decimal) -> bool:
    """Whether ``price`` has traded ``tick`` past ``level`` in the adverse direction.

    Direction is inferred from the comparison rather than passed in: below ``level`` the
    adverse direction is down, above it is up. A price exactly *at* the level is not past it;
    one that has travelled ``tick`` or more beyond it is. The comparison is inclusive at the
    boundary, because "at least one tick of travel" means reaching ``level + tick`` is enough.
    """
    if not isinstance(price, Decimal) or not isinstance(level, Decimal):
        raise ValueError("price and level must be Decimals")
    if not isinstance(tick, Decimal) or tick <= 0:
        raise ValueError(f"tick must be a positive Decimal, got {tick!r}")
    return price <= level - tick or price >= level + tick


def entry_price_for(side: Any, bar: Any, kind: FillKind) -> Decimal | None:
    """The bar price a stop or market order would reference, before slippage.

    * buy market / short stop -> the ask high (worst price a buyer can pay)
    * sell market / long stop -> the bid low (worst price a seller can receive)

    Limits are excluded: their price is guaranteed by the order, so they never reference the
    bar extreme.
    """
    from engine.domain.orders import OrderSide

    if kind in {FillKind.LIMIT}:
        return None

    if side is OrderSide.BUY:
        return bar.ask_ohlc.high
    if side is OrderSide.SELL:
        return bar.bid_ohlc.low
    raise ValueError(f"side must be BUY or SELL, got {side!r}")


class SimulatedBroker:
    """Executes orders against completed bars, pessimistically.

    The broker is stateless apart from its slippage generator and latency, so feeding the
    same bars in the same order always produces the same fills.
    """

    def __init__(
        self,
        *,
        run_id: str,
        latency_ms: int = 0,
        max_slippage_ticks: int = 8,
        slippage: SeededSlippage | None = None,
    ) -> None:
        if isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or latency_ms < 0:
            raise ValueError(
                f"latency_ms must be a non-negative int, got {latency_ms!r}"
            )
        self._run_id = run_id
        self._latency = dt.timedelta(milliseconds=latency_ms)
        self._slippage = slippage or SeededSlippage(
            run_id=run_id, max_ticks=max_slippage_ticks
        )

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def latency(self) -> dt.timedelta:
        return self._latency

    @property
    def slippage(self) -> SeededSlippage:
        return self._slippage

    # -- public API ---------------------------------------------------------- #
    def submit_market(
        self,
        bar: Any,
        side: Any,
        quantity: Decimal,
        *,
        order_id: str,
        tick: Decimal,
    ) -> OrderOutcome:
        """A market order always fills, at the bar extreme plus adverse slippage."""
        reference = entry_price_for(side, bar, FillKind.MARKET)
        if reference is None:  # pragma: no cover - MARKET always references a price
            raise ValueError("a market order must have a reference price")
        price = self._slippage.apply(
            side=side, reference=reference, order_id=order_id
        )
        fill = self._fill(
            order_id=order_id,
            side=side,
            kind=FillKind.MARKET,
            quantity=quantity,
            reference=reference,
            price=price,
            bar=bar,
        )
        return OrderOutcome(filled=True, fill=fill)

    def submit_limit(
        self,
        bar: Any,
        side: Any,
        quantity: Decimal,
        level: Decimal,
        *,
        order_id: str,
        tick: Decimal,
    ) -> OrderOutcome:
        """A limit order fills only when the price trades a full tick through the level."""
        if not isinstance(level, Decimal):
            raise ValueError(f"level must be a Decimal, got {type(level).__name__}")
        if not isinstance(tick, Decimal) or tick <= 0:
            raise ValueError(f"tick must be a positive Decimal, got {tick!r}")

        from engine.domain.orders import OrderSide

        if side is OrderSide.BUY:
            # A buy limit rests below the market; the ask must fall a tick past it.
            traded_through = bar.ask_ohlc.low <= level - tick
        else:
            traded_through = bar.bid_ohlc.high >= level + tick

        if not traded_through:
            return OrderOutcome(filled=False, reason="limit not traded through")

        # A limit's price is guaranteed, so no slippage is charged.
        fill = self._fill(
            order_id=order_id,
            side=side,
            kind=FillKind.LIMIT,
            quantity=quantity,
            reference=level,
            price=level,
            bar=bar,
            slippage_ticks=0,
        )
        return OrderOutcome(filled=True, fill=fill)

    def submit_stop(
        self,
        bar: Any,
        side: Any,
        quantity: Decimal,
        level: Decimal,
        *,
        order_id: str,
        tick: Decimal,
    ) -> OrderOutcome:
        """A stop triggers when the price reaches it, then fills as a market order."""
        if not isinstance(level, Decimal):
            raise ValueError(f"level must be a Decimal, got {type(level).__name__}")
        if not isinstance(tick, Decimal) or tick <= 0:
            raise ValueError(f"tick must be a positive Decimal, got {tick!r}")

        from engine.domain.orders import OrderSide

        # A resting stop is *triggered* by a touch, not by a tick of travel: the moment the
        # price reaches the level, the order becomes a market order and is filled at the
        # bar extreme. Requiring a full tick of overshoot here would let a stop-loss sit
        # unfilled while the price ran straight through it, which is optimistic.
        if side is OrderSide.SELL:
            # A long stop-loss sits below the market and triggers on the bid.
            triggered = bar.bid_ohlc.low <= level
        else:
            # A short stop sits above the market and triggers on the ask.
            triggered = bar.ask_ohlc.high >= level

        if not triggered:
            return OrderOutcome(filled=False, reason="stop not triggered")

        reference = entry_price_for(side, bar, FillKind.STOP)
        if reference is None:  # pragma: no cover - STOP always references a price
            raise ValueError("a stop order must have a reference price")
        price = self._slippage.apply(
            side=side, reference=reference, order_id=order_id
        )
        fill = self._fill(
            order_id=order_id,
            side=side,
            kind=FillKind.STOP,
            quantity=quantity,
            reference=reference,
            price=price,
            bar=bar,
        )
        return OrderOutcome(filled=True, fill=fill)

    def evaluate_bar_with_stop(
        self,
        bar: Any,
        *,
        entry_level: Decimal,
        stop_level: Decimal,
        order_id: str,
        tick: Decimal,
    ) -> IntraBarOutcome:
        """Evaluate one bar for a limit entry paired with its stop-loss.

        The bar is checked for both conditions. If it reaches the entry *and* the stop, the
        entry is assumed filled and immediately stopped out, because assuming the favourable
        ordering would flatter the backtest. If it reaches only the entry, the trade is
        recorded as entry-only and left open for the next bar to resolve.
        """
        if not isinstance(entry_level, Decimal) or not isinstance(stop_level, Decimal):
            raise ValueError("entry_level and stop_level must be Decimals")
        if not isinstance(tick, Decimal) or tick <= 0:
            raise ValueError(f"tick must be a positive Decimal, got {tick!r}")

        from engine.domain.orders import OrderSide

        # A limit entry still needs a full tick of travel to fill.
        entry_reached = bar.ask_ohlc.low <= entry_level - tick
        stop_reached = bar.bid_ohlc.low <= stop_level

        if not entry_reached:
            return IntraBarOutcome(entry_filled=False, stopped_out=False)

        entry_fill = self._fill(
            order_id=order_id,
            side=OrderSide.BUY,
            kind=FillKind.LIMIT,
            quantity=Decimal(1),
            reference=entry_level,
            price=entry_level,
            bar=bar,
            slippage_ticks=0,
        )

        if not stop_reached:
            return IntraBarOutcome(
                entry_filled=True,
                stopped_out=False,
                entry_time=bar.timestamp_utc,
                entry_fill=entry_fill,
            )

        exit_reference = bar.bid_ohlc.low
        exit_price = self._slippage.apply(
            side=OrderSide.SELL, reference=exit_reference, order_id=f"{order_id}-exit"
        )
        exit_fill = self._fill(
            order_id=f"{order_id}-exit",
            side=OrderSide.SELL,
            kind=FillKind.STOP_LOSS,
            quantity=Decimal(1),
            reference=exit_reference,
            price=exit_price,
            bar=bar,
        )
        realized = exit_price - entry_level
        return IntraBarOutcome(
            entry_filled=True,
            stopped_out=True,
            entry_time=bar.timestamp_utc,
            exit_time=bar.timestamp_utc,
            realized=realized,
            entry_fill=entry_fill,
            exit_fill=exit_fill,
        )

    # -- internals ------------------------------------------------------------ #
    def _fill(
        self,
        *,
        order_id: str,
        side: Any,
        kind: FillKind,
        quantity: Decimal,
        reference: Decimal,
        price: Decimal,
        bar: Any,
        slippage_ticks: int | None = None,
    ) -> Fill:
        if slippage_ticks is None:
            slippage_ticks = self._slippage.for_order(order_id)
        return Fill(
            order_id=order_id,
            run_id=self._run_id,
            side=side,
            kind=kind,
            quantity=quantity,
            reference_price=reference,
            price=price,
            slippage_ticks=slippage_ticks,
            slippage=abs(price - reference),
            seed=derive_seed(self._run_id, order_id),
            filled_at=bar.timestamp_utc + self._latency,
            bar_start=bar.timestamp_utc,
        )
