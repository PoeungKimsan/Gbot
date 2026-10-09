"""Unit tests for :mod:`engine.execution.simulator`.

These are the tests that decide whether a backtest is honest. Every rule from the phase is
pinned, and each one is the *pessimistic* reading:

* buys fill on the ask, sells on the bid;
* a long stop triggers on the bid, a short stop on the ask;
* a limit fills only when the price trades *through* its level by a full tick;
* when a bar touches both the limit entry and the stop, the entry is assumed filled and then
  stopped out in the same bar.
"""

import datetime as dt
from decimal import Decimal

import pytest

from engine.domain.orders import OrderSide
from engine.execution.simulator import (
    FillKind,
    SeededSlippage,
    SimulatedBroker,
    derive_seed,
    is_price_past_level,
    slippage_seed,
)

UTC = dt.UTC
TICK = Decimal("0.01")


def _bar(
    timestamp: str,
    *,
    bid: tuple[str, str, str, str],
    ask: tuple[str, str, str, str] | None = None,
    timeframe: str = "M1",
) -> object:
    """Build a Bar from bid/ask OHLC strings."""
    from engine.market.models import OHLC, Bar, Timeframe

    ask = ask or tuple(str(Decimal(v) + TICK) for v in bid)
    return Bar(
        timeframe=Timeframe.M1 if timeframe == "M1" else Timeframe.M5,
        timestamp_utc=dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
        complete=True,
        volume=1,
        bid_ohlc=OHLC(*(Decimal(v) for v in bid)),
        ask_ohlc=OHLC(*(Decimal(v) for v in ask)),
    )


def _bar_14_30(bid, ask=None) -> object:
    return _bar("2026-01-15T14:30:00Z", bid=bid, ask=ask)


def _bar_14_31(bid, ask=None) -> object:
    return _bar("2026-01-15T14:31:00Z", bid=bid, ask=ask)


# --------------------------------------------------------------------------- #
# seed derivation
# --------------------------------------------------------------------------- #
def test_seed_is_a_stable_function_of_run_and_order() -> None:
    assert derive_seed("run-1", "ord-1") == derive_seed("run-1", "ord-1")


def test_seed_differs_by_run() -> None:
    assert derive_seed("run-1", "ord-1") != derive_seed("run-2", "ord-1")


def test_seed_differs_by_order() -> None:
    assert derive_seed("run-1", "ord-1") != derive_seed("run-1", "ord-2")


def test_seed_comes_from_the_first_eight_hash_bytes() -> None:
    import hashlib

    expected = int.from_bytes(
        hashlib.sha256(b"run-1:ord-1").digest()[:8], "big"
    )
    assert derive_seed("run-1", "ord-1") == expected


def test_slippage_seed_matches_derive_seed() -> None:
    assert slippage_seed("run-1", "ord-1") == derive_seed("run-1", "ord-1")


# --------------------------------------------------------------------------- #
# slippage
# --------------------------------------------------------------------------- #
def test_slippage_is_deterministic() -> None:
    slipper = SeededSlippage(run_id="run-1", max_ticks=8)

    first = slipper.for_order("ord-1")
    second = slipper.for_order("ord-1")

    assert first == second


def test_slippage_is_adverse_for_both_sides() -> None:
    """A buy pays more than the reference; a sell receives less."""
    slipper = SeededSlippage(run_id="run-1", max_ticks=8)
    ticks = slipper.for_order("ord-1")

    slipper_for_entry = slipper.apply(
        side=OrderSide.BUY, reference=Decimal("2038.00"), order_id="ord-1"
    )
    slipper_for_exit = slipper.apply(
        side=OrderSide.SELL, reference=Decimal("2038.00"), order_id="ord-1"
    )

    assert slipper_for_entry > Decimal("2038.00")
    assert slipper_for_exit < Decimal("2038.00")
    assert ticks >= 0


def test_slippage_never_exceeds_the_maximum() -> None:
    slipper = SeededSlippage(run_id="run-1", max_ticks=8)

    for index in range(200):
        ticks = slipper.for_order(f"ord-{index}")
        assert 0 <= ticks <= 8


def test_slippage_returns_integers_not_floats() -> None:
    slipper = SeededSlippage(run_id="run-1", max_ticks=8)

    for index in range(50):
        assert isinstance(slipper.for_order(f"ord-{index}"), int)


def test_slippage_rejects_a_negative_maximum() -> None:
    with pytest.raises(ValueError, match="max_ticks"):
        SeededSlippage(run_id="run-1", max_ticks=-1)


def test_slippage_is_reproducible_across_instances() -> None:
    """The same seed must give the same slippage on a fresh instance."""
    first = SeededSlippage(run_id="run-1", max_ticks=8).for_order("ord-1")
    second = SeededSlippage(run_id="run-1", max_ticks=8).for_order("ord-1")

    assert first == second


# --------------------------------------------------------------------------- #
# price-past-level gating
# --------------------------------------------------------------------------- #
def test_limit_entry_requires_a_full_tick_of_adverse_travel() -> None:
    """A touch at exactly the level is not a fill."""
    level = Decimal("2038.00")

    # Exactly at the level: a touch, not a fill.
    assert is_price_past_level(Decimal("2038.00"), level, TICK) is False
    # One tick of travel: enough.
    assert is_price_past_level(Decimal("2038.01"), level, TICK) is True
    assert is_price_past_level(Decimal("2037.99"), level, TICK) is True
    # Half a tick: still not enough.
    assert is_price_past_level(Decimal("2037.995"), level, TICK) is False


# --------------------------------------------------------------------------- #
# market fills
# --------------------------------------------------------------------------- #
def test_market_buy_fills_on_the_ask() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.BUY, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert outcome.filled is True
    # The ask side of this bar is the bid high plus one tick: 2038.51.
    assert outcome.fill.reference_price == Decimal("2038.51")
    assert outcome.fill.side is OrderSide.BUY


def test_market_sell_fills_on_the_bid() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.SELL, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert outcome.fill.reference_price == Decimal("2038.00")


def test_buy_fill_price_is_worse_than_the_ask() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.BUY, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert outcome.fill.price > outcome.fill.reference_price


def test_sell_fill_price_is_worse_than_the_bid() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.SELL, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert outcome.fill.price < outcome.fill.reference_price


# --------------------------------------------------------------------------- #
# limits
# --------------------------------------------------------------------------- #
def test_long_limit_fills_when_the_ask_trades_through() -> None:
    """A buy limit only fills when the ask goes a full tick past the level."""
    broker = SimulatedBroker(run_id="run-1")
    # The ask low is 2037.00, a full tick below the 2038.00 level: a genuine fill.
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2037.00", "2038.25"))

    outcome = broker.submit_limit(
        bar,
        OrderSide.BUY,
        Decimal("1"),
        Decimal("2038.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.filled is True
    assert outcome.fill is not None
    assert outcome.fill.price == Decimal("2038.00")


def test_long_limit_does_not_fill_on_a_mere_touch() -> None:
    broker = SimulatedBroker(run_id="run-1")
    # The ask low is exactly 2038.00 -- a touch, not a trade through.
    bar = _bar_14_30(bid=("2038.10", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_limit(
        bar,
        OrderSide.BUY,
        Decimal("1"),
        Decimal("2038.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.filled is False


def test_short_limit_fills_when_the_bid_trades_through() -> None:
    """A sell limit is above the market, so it needs the bid to rise past it."""
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2040.02", "2038.00", "2039.50"))

    outcome = broker.submit_limit(
        bar,
        OrderSide.SELL,
        Decimal("1"),
        Decimal("2040.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.filled is True
    assert outcome.fill is not None
    assert outcome.fill.price == Decimal("2040.00")


def test_short_limit_does_not_fill_on_a_mere_touch() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2040.00", "2038.00", "2039.50"))

    outcome = broker.submit_limit(
        bar,
        OrderSide.SELL,
        Decimal("1"),
        Decimal("2040.01"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.filled is False


def test_limit_fill_is_at_the_limit_price_without_slippage() -> None:
    """A limit order's price is guaranteed, so no slippage is charged."""
    broker = SimulatedBroker(run_id="run-1")
    # A buy limit at 2038.00 with the ask falling to 2037.90: one tick of travel.
    bar = _bar_14_30(
        bid=("2037.90", "2038.20", "2037.89", "2038.10"),
        ask=("2037.91", "2038.21", "2037.90", "2038.11"),
    )

    outcome = broker.submit_limit(
        bar,
        OrderSide.BUY,
        Decimal("1"),
        Decimal("2038.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.filled is True
    assert outcome.fill is not None
    assert outcome.fill.price == Decimal("2038.00")
    assert outcome.fill.slippage == Decimal(0)


# --------------------------------------------------------------------------- #
# stops
# --------------------------------------------------------------------------- #
def test_long_stop_triggers_on_the_bid() -> None:
    """A long stop-loss sits below the market, so the bid hitting it triggers."""
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2030.00", "2038.25"))

    outcome = broker.submit_stop(
        bar, OrderSide.SELL, Decimal("1"), Decimal("2030.00"), order_id="ord-1", tick=TICK
    )

    assert outcome.filled is True
    # The reference is the bar's worst bid, which is worse than the stop level.
    assert outcome.fill.reference_price == Decimal("2030.00")


def test_short_stop_triggers_on_the_ask() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2050.00", "2038.00", "2049.00"))

    outcome = broker.submit_stop(
        bar, OrderSide.BUY, Decimal("1"), Decimal("2050.00"), order_id="ord-1", tick=TICK
    )

    assert outcome.filled is True
    assert outcome.fill.reference_price == Decimal("2050.01")


def test_stop_does_not_trigger_below_the_level() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2037.90", "2038.25"))

    outcome = broker.submit_stop(
        bar, OrderSide.SELL, Decimal("1"), Decimal("2030.00"), order_id="ord-1", tick=TICK
    )

    assert outcome.filled is False


def test_stop_fill_price_is_worse_than_the_reference() -> None:
    """A sell stop fills below the bar's worst bid; a buy stop fills above."""
    broker = SimulatedBroker(run_id="run-1")
    bar_sell = _bar_14_30(bid=("2038.00", "2038.50", "2030.00", "2038.25"))
    outcome_sell = broker.submit_stop(
        bar_sell, OrderSide.SELL, Decimal("1"), Decimal("2030.00"), order_id="sell", tick=TICK
    )

    bar_buy = _bar_14_30(bid=("2038.00", "2050.00", "2038.00", "2049.00"))
    outcome_buy = broker.submit_stop(
        bar_buy, OrderSide.BUY, Decimal("1"), Decimal("2050.00"), order_id="buy", tick=TICK
    )

    assert outcome_sell.fill is not None
    assert outcome_buy.fill is not None
    assert outcome_sell.fill.price < outcome_sell.fill.reference_price
    assert outcome_buy.fill.price > outcome_buy.fill.reference_price


# --------------------------------------------------------------------------- #
# intra-bar ambiguity
# --------------------------------------------------------------------------- #
def test_limit_entry_then_stop_in_one_bar_is_assumed_stopped_out() -> None:
    """The rule that decides whether a backtest is honest.

    When a single bar spans both the limit entry and the stop-loss, the entry is assumed to
    fill and then be stopped out immediately. Any other reading would credit the trade with
    the favourable outcome that happened to be printed first.
    """
    broker = SimulatedBroker(run_id="run-1")
    # The ask runs from 2038.52 down to 2037.00: it crosses the 2038.50 entry and the
    # 2030.00 stop in the same bar.
    bar = _bar_14_30(
        bid=("2038.00", "2038.50", "2030.00", "2030.00"),
        ask=("2038.52", "2038.52", "2030.01", "2030.01"),
    )

    outcome = broker.evaluate_bar_with_stop(
        bar,
        entry_level=Decimal("2038.50"),
        stop_level=Decimal("2030.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.entry_filled is True
    assert outcome.stopped_out is True
    assert outcome.entry_time == bar.timestamp_utc
    assert outcome.exit_time == bar.timestamp_utc
    assert outcome.realized < Decimal(0)


def test_entry_alone_without_touching_the_stop_is_not_stopped_out() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2037.50", "2038.00"),
                     ask=("2038.52", "2038.52", "2037.51", "2038.01"))

    outcome = broker.evaluate_bar_with_stop(
        bar,
        entry_level=Decimal("2038.50"),
        stop_level=Decimal("2030.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.entry_filled is True
    assert outcome.stopped_out is False


def test_no_entry_and_no_stop_means_no_fill() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2037.90", "2038.25"))

    outcome = broker.evaluate_bar_with_stop(
        bar,
        entry_level=Decimal("2030.00"),
        stop_level=Decimal("2029.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.entry_filled is False
    assert outcome.stopped_out is False


def test_stop_alone_without_an_entry_is_not_a_trade() -> None:
    broker = SimulatedBroker(run_id="run-1")
    # The bid reaches the stop, but the ask never gets a full tick below the entry level,
    # so the entry order is still resting and cannot have been stopped out.
    bar = _bar_14_30(
        bid=("2038.60", "2038.60", "2030.00", "2038.55"),
        ask=("2038.61", "2038.61", "2038.55", "2038.56"),
    )

    outcome = broker.evaluate_bar_with_stop(
        bar,
        entry_level=Decimal("2038.50"),
        stop_level=Decimal("2030.00"),
        order_id="ord-1",
        tick=TICK,
    )

    assert outcome.entry_filled is False
    assert outcome.stopped_out is False
    assert outcome.realized == Decimal(0)


# --------------------------------------------------------------------------- #
# latency
# --------------------------------------------------------------------------- #
def test_latency_shifts_the_fill_timestamp() -> None:
    broker = SimulatedBroker(run_id="run-1", latency_ms=250)
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.BUY, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert outcome.fill.filled_at == bar.timestamp_utc + dt.timedelta(milliseconds=250)


def test_zero_latency_leaves_the_timestamp_alone() -> None:
    broker = SimulatedBroker(run_id="run-1", latency_ms=0)
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.BUY, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert outcome.fill.filled_at == bar.timestamp_utc


def test_negative_latency_is_rejected() -> None:
    with pytest.raises(ValueError, match="latency"):
        SimulatedBroker(run_id="run-1", latency_ms=-1)


# --------------------------------------------------------------------------- #
# bookkeeping
# --------------------------------------------------------------------------- #
def test_every_fill_records_its_seed_and_slippage() -> None:
    """The journal seed must be recoverable from the fill, not recomputed differently."""
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2030.00", "2038.25"))

    outcome = broker.submit_stop(
        bar, OrderSide.SELL, Decimal("1"), Decimal("2030.00"), order_id="ord-1", tick=TICK
    )

    assert outcome.fill.slippage_ticks >= 0
    assert outcome.fill.seed == derive_seed("run-1", "ord-1")
    assert outcome.fill.slippage >= Decimal(0)


def test_fill_records_the_run_and_bar_it_came_from() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.BUY, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert outcome.fill.run_id == "run-1"
    assert outcome.fill.order_id == "ord-1"
    assert outcome.fill.bar_start == bar.timestamp_utc
    assert outcome.fill.kind is FillKind.MARKET


def test_entry_and_exit_prices_are_decimal() -> None:
    broker = SimulatedBroker(run_id="run-1")
    bar = _bar_14_30(bid=("2038.00", "2038.50", "2038.00", "2038.25"))

    outcome = broker.submit_market(
        bar, OrderSide.BUY, Decimal("1"), order_id="ord-1", tick=TICK
    )

    assert isinstance(outcome.fill.price, Decimal)
    assert isinstance(outcome.fill.reference_price, Decimal)
    assert isinstance(outcome.fill.slippage, Decimal)
