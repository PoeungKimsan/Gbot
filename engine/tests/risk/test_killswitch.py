"""Tests for the latching kill-switch and its journal persistence.

Three properties are load-bearing, and each one has cost a real account money somewhere:

1. **The latch holds.** Reaching ``daily_loss_limit_r`` trips the switch, and *nothing*
   short of the next trading day's rollover clears it. A profitable trade, a restart, or
   a hopeful caller cannot.
2. **The latch survives a restart.** The state is written as a ``RISK_KILLSWITCH`` journal
   event, so a process that dies mid-day comes back up still tripped. A latch that lived
   only in memory would let the engine re-enter a market it already gave up on for the day.
3. **Tripping blocks entries and cancels resting limits.** Exits stay allowed: blocking an
   exit is how a stop becomes a blow-up.

The journal is used through its real write path (the thread-isolated worker) and read back
through a read-only connection, so these tests exercise the same bytes a restart would.
"""

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from engine.domain.events import DomainEvent, EventType
from engine.domain.orders import Order, OrderKind, OrderRequest, OrderSide, OrderStatus, transition
from engine.journal.schema import verify_chain
from engine.journal.writer import JournalWriter
from engine.market.instrument import InstrumentSpec
from engine.risk.config import RiskConfig
from engine.risk.killswitch import (
    KillSwitch,
    KillSwitchState,
    OrderRole,
    RiskAction,
    RiskDecision,
)

UTC = dt.UTC
XAU_USD = InstrumentSpec(
    name="XAU_USD",
    pip_location=-4,
    display_precision=2,
    trade_units_precision=5,
    minimum_trade_size=Decimal("1"),
)
CONFIG = RiskConfig(risk_per_trade=Decimal("0.01"), daily_loss_limit_r=Decimal("3"))

#: Thursday, before the 17:00 New York rollover: still the 2026-01-14 trading day.
THURSDAY = dt.datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
#: The next trading day's rollover instant.
NEXT_DAY = dt.datetime(2026, 1, 16, 22, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# local doubles and the journal adapter
# --------------------------------------------------------------------------- #
class FakeClock:
    def __call__(self) -> float:
        return 0.0


class FakeSleeper:
    async def __call__(self, seconds: float) -> None:
        return None


class JournalSink:
    """Adapts the thread-isolated writer to the kill-switch's sink protocol."""

    def __init__(self, writer: JournalWriter) -> None:
        self._writer = writer

    def submit(self, event: DomainEvent) -> None:
        self._writer.submit(event)


def _read_killswitch_events(writer: JournalWriter) -> list[DomainEvent]:
    """Replay the switch events out of the journal, the way a restart would."""
    connection = writer.connect_readonly()
    try:
        rows = connection.execute(
            "SELECT event_id, event_type, occurred_at, payload FROM journal"
            " WHERE event_type = ? ORDER BY sequence",
            (EventType.RISK_KILLSWITCH.value,),
        ).fetchall()
    finally:
        connection.close()

    return [
        DomainEvent(
            event_id=row["event_id"],
            event_type=EventType(row["event_type"]),
            occurred_at=dt.datetime.fromisoformat(row["occurred_at"]),
            payload=json.loads(row["payload"])["payload"],
        )
        for row in rows
    ]


def _writer(tmp_path: Path) -> JournalWriter:
    return JournalWriter(
        path=tmp_path / "journal.db",
        instrument=XAU_USD,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        flush_interval=dt.timedelta(milliseconds=1),
    )


def _request(
    *,
    side: OrderSide = OrderSide.BUY,
    kind: OrderKind = OrderKind.MARKET,
    limit_price: Decimal | None = None,
) -> OrderRequest:
    return OrderRequest(
        instrument="XAU_USD",
        side=side,
        kind=kind,
        quantity=Decimal("1"),
        submitted_at=THURSDAY,
        limit_price=limit_price,
    )


def _market_entry(order_id: str = "ord-1") -> Order:
    return Order(order_id=order_id, run_id="run-1", request=_request())


def _resting_limit(order_id: str = "ord-2") -> Order:
    """A limit that reached the venue and is now waiting for a fill."""
    order = Order(
        order_id=order_id,
        run_id="run-1",
        request=_request(
            side=OrderSide.SELL,
            kind=OrderKind.LIMIT,
            limit_price=Decimal("2100.50"),
        ),
    )
    return transition(order, OrderStatus.ACKNOWLEDGED, at=THURSDAY)


def _tripped_switch(**overrides: object) -> KillSwitch:
    """A switch already at the daily limit."""
    switch = KillSwitch(config=CONFIG, **overrides)  # type: ignore[arg-type]
    switch.record_realized_r(Decimal("-1.5"), THURSDAY)
    switch.record_realized_r(Decimal("-1.5"), THURSDAY)
    return switch


# --------------------------------------------------------------------------- #
# the latch
# --------------------------------------------------------------------------- #
def test_a_new_switch_is_armed() -> None:
    switch = KillSwitch(config=CONFIG)

    assert switch.state is KillSwitchState.ARMED
    assert switch.is_tripped is False
    assert switch.trading_day is None


def test_a_loss_below_the_limit_does_not_trip() -> None:
    switch = KillSwitch(config=CONFIG)

    switch.record_realized_r(Decimal("-2.5"), THURSDAY)

    assert switch.state is KillSwitchState.ARMED
    assert switch.is_tripped is False


def test_reaching_the_limit_exactly_trips_the_latch() -> None:
    """Inclusive: -3R is a breach, not "nearly a breach"."""
    switch = KillSwitch(config=CONFIG)

    switch.record_realized_r(Decimal("-1.5"), THURSDAY)
    switch.record_realized_r(Decimal("-1.5"), THURSDAY)

    assert switch.state is KillSwitchState.TRIPPED
    assert switch.is_tripped is True
    assert switch.trading_day == dt.date(2026, 1, 14)


def test_one_trade_exceeding_the_limit_trips() -> None:
    switch = KillSwitch(config=CONFIG)

    switch.record_realized_r(Decimal("-3.5"), THURSDAY)

    assert switch.is_tripped is True


def test_a_profitable_trade_after_tripping_does_not_clear_the_latch() -> None:
    switch = _tripped_switch()

    switch.record_realized_r(Decimal("5"), THURSDAY)

    assert switch.is_tripped is True


def test_a_loss_beyond_the_limit_does_not_trip_twice() -> None:
    """The latch is already set; a deeper loss changes nothing."""
    switch = _tripped_switch()

    switch.record_realized_r(Decimal("-2"), THURSDAY)

    assert switch.is_tripped is True
    assert switch.loss_r == Decimal("-5")


def test_a_reset_inside_the_same_day_is_refused() -> None:
    """The only thing that clears the latch is the trading day rolling over."""
    switch = _tripped_switch()

    switch.reset(THURSDAY)

    assert switch.is_tripped is True


def test_the_latch_reports_why_it_tripped() -> None:
    switch = _tripped_switch()

    assert "3" in switch.trip_reason


def test_a_trade_recorded_through_pnl_converts_to_r() -> None:
    switch = KillSwitch(config=CONFIG)

    # -1500 on a 1000 risked is -1.5R.
    switch.record_trade(Decimal("-1500"), risk_amount=Decimal("1000"), at=THURSDAY)

    assert switch.loss_r == Decimal("-1.5")


# --------------------------------------------------------------------------- #
# blocking
# --------------------------------------------------------------------------- #
def test_an_entry_is_allowed_while_armed() -> None:
    switch = KillSwitch(config=CONFIG)
    decision = switch.adjudicate(_market_entry(), OrderRole.ENTRY, THURSDAY)

    assert decision.action is RiskAction.ALLOW


def test_a_new_entry_is_blocked_while_tripped() -> None:
    switch = _tripped_switch()
    decision = switch.adjudicate(_market_entry(), OrderRole.ENTRY, THURSDAY)

    assert decision.action is RiskAction.BLOCK
    assert decision.order_id == "ord-1"


def test_a_resting_limit_is_cancelled_while_tripped() -> None:
    switch = _tripped_switch()
    decision = switch.adjudicate(_resting_limit(), OrderRole.ENTRY, THURSDAY)

    assert decision.action is RiskAction.CANCEL


def test_a_cancel_decision_is_executable_against_the_domain_model() -> None:
    """The decision must be actionable by the caller, not merely descriptive."""
    switch = _tripped_switch()
    order = _resting_limit()

    decision = switch.adjudicate(order, OrderRole.ENTRY, THURSDAY)
    assert decision.action is RiskAction.CANCEL

    cancelled = transition(order, OrderStatus.CANCELLED, at=THURSDAY)

    assert cancelled.status is OrderStatus.CANCELLED
    assert cancelled.cancelled_at == THURSDAY


def test_a_market_order_that_never_reached_the_venue_is_blocked_not_cancelled() -> None:
    """PENDING means it was never sent, so there is nothing to cancel."""
    switch = _tripped_switch()
    decision = switch.adjudicate(_market_entry(), OrderRole.ENTRY, THURSDAY)

    assert decision.action is RiskAction.BLOCK


def test_an_exit_is_allowed_even_while_tripped() -> None:
    """Blocking an exit is how a stop becomes a blow-up."""
    switch = _tripped_switch()

    decision = switch.adjudicate(_market_entry(), OrderRole.EXIT, THURSDAY)

    assert decision.action is RiskAction.ALLOW


def test_a_resting_exit_limit_is_left_alone_while_tripped() -> None:
    switch = _tripped_switch()

    decision = switch.adjudicate(_resting_limit(), OrderRole.EXIT, THURSDAY)

    assert decision.action is RiskAction.ALLOW


def test_a_decision_carries_the_switch_state() -> None:
    switch = _tripped_switch()

    decision = switch.adjudicate(_market_entry(), OrderRole.ENTRY, THURSDAY)

    assert isinstance(decision, RiskDecision)
    assert decision.state is KillSwitchState.TRIPPED


# --------------------------------------------------------------------------- #
# rollover
# --------------------------------------------------------------------------- #
def test_the_rollover_re_arms_the_switch() -> None:
    switch = _tripped_switch()

    assert switch.evaluate(NEXT_DAY) is KillSwitchState.ARMED
    assert switch.is_tripped is False
    assert switch.trading_day is None


def test_an_entry_is_allowed_after_the_rollover() -> None:
    switch = _tripped_switch()
    switch.evaluate(NEXT_DAY)

    decision = switch.adjudicate(_market_entry(), OrderRole.ENTRY, NEXT_DAY)

    assert decision.action is RiskAction.ALLOW


def test_evaluate_does_nothing_before_the_rollover() -> None:
    switch = _tripped_switch()

    assert switch.evaluate(THURSDAY) is KillSwitchState.TRIPPED


def test_a_new_day_that_trips_again_stays_tripped() -> None:
    switch = _tripped_switch()
    switch.evaluate(NEXT_DAY)

    switch.record_realized_r(Decimal("-3"), NEXT_DAY)

    assert switch.is_tripped is True
    assert switch.trading_day == dt.date(2026, 1, 16)


# --------------------------------------------------------------------------- #
# journal persistence across a restart
# --------------------------------------------------------------------------- #
def test_tripping_without_a_sink_still_works_in_memory() -> None:
    switch = _tripped_switch()

    assert switch.is_tripped is True


def test_the_latch_is_written_to_the_journal(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))

    switch.record_realized_r(Decimal("-1.5"), THURSDAY)
    switch.record_realized_r(Decimal("-1.5"), THURSDAY)
    assert writer.drain()

    events = _read_killswitch_events(writer)
    writer.stop()

    assert len(events) == 1
    assert events[0].event_type is EventType.RISK_KILLSWITCH
    assert events[0].payload["state"] == "TRIPPED"


def test_the_journal_events_still_form_a_valid_chain(tmp_path: Path) -> None:
    """A kill-switch event is an ordinary journalled fact, chain and all."""
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    assert writer.drain()
    writer.stop()

    connection = writer.connect_readonly()
    verification = verify_chain(connection)
    connection.close()

    assert verification.is_valid, verification.reason


def test_a_restarted_process_comes_up_tripped(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    assert writer.drain()
    events = _read_killswitch_events(writer)
    writer.stop()

    # The restart: a brand new object, rebuilt from the journal alone.
    restored = KillSwitch.restore(events, config=CONFIG, at=THURSDAY)

    assert restored.state is KillSwitchState.TRIPPED
    assert restored.trading_day == dt.date(2026, 1, 14)
    decision = restored.adjudicate(_market_entry(), OrderRole.ENTRY, THURSDAY)
    assert decision.action is RiskAction.BLOCK


def test_a_restored_switch_cancels_resting_limits_too(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    assert writer.drain()
    events = _read_killswitch_events(writer)
    writer.stop()

    restored = KillSwitch.restore(events, config=CONFIG, at=THURSDAY)

    assert restored.adjudicate(_resting_limit(), OrderRole.ENTRY, THURSDAY).action is (
        RiskAction.CANCEL
    )


def test_a_restart_after_the_rollover_comes_up_armed(tmp_path: Path) -> None:
    """The switch stayed tripped overnight; the new trading day is what clears it."""
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    assert writer.drain()
    events = _read_killswitch_events(writer)
    writer.stop()

    restored = KillSwitch.restore(events, config=CONFIG, at=NEXT_DAY)

    assert restored.state is KillSwitchState.ARMED
    assert restored.adjudicate(_market_entry(), OrderRole.ENTRY, NEXT_DAY).action is (
        RiskAction.ALLOW
    )


def test_a_restart_with_no_events_comes_up_armed(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    writer.stop()

    restored = KillSwitch.restore([], config=CONFIG, at=THURSDAY)

    assert restored.state is KillSwitchState.ARMED


def test_a_re_arm_after_a_restore_is_recorded(tmp_path: Path) -> None:
    """The rollover that happened while the process was down is journalled, not lost."""
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    assert writer.drain()
    events = _read_killswitch_events(writer)

    restored = KillSwitch.restore(events, config=CONFIG, at=NEXT_DAY, sink=JournalSink(writer))
    assert writer.drain()

    journalled = _read_killswitch_events(writer)
    writer.stop()

    assert len(journalled) == 2
    assert journalled[-1].payload["state"] == "ARMED"
    assert restored.evaluate(NEXT_DAY) is KillSwitchState.ARMED


def test_a_trip_and_re_arm_are_two_journal_events(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    switch.evaluate(NEXT_DAY)
    assert writer.drain()

    states = [event.payload["state"] for event in _read_killswitch_events(writer)]
    writer.stop()

    assert states == ["TRIPPED", "ARMED"]


def test_a_restore_folds_a_full_history(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    switch.evaluate(NEXT_DAY)
    switch.record_realized_r(Decimal("-3"), NEXT_DAY)
    assert writer.drain()
    events = _read_killswitch_events(writer)
    writer.stop()

    restored = KillSwitch.restore(events, config=CONFIG, at=NEXT_DAY)

    assert restored.state is KillSwitchState.TRIPPED
    assert restored.trading_day == dt.date(2026, 1, 16)


def test_the_tripped_event_records_the_day_and_the_limit(tmp_path: Path) -> None:
    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))
    switch.record_realized_r(Decimal("-3"), THURSDAY)
    assert writer.drain()
    events = _read_killswitch_events(writer)
    writer.stop()

    payload = events[0].payload

    assert payload["state"] == "TRIPPED"
    assert payload["trading_day"] == "2026-01-14"
    assert payload["limit_r"] == "3"
    assert payload["loss_r"] == "-3"


def test_the_killswitch_events_are_submitted_not_written_inline(tmp_path: Path) -> None:
    """AGENTS.md 2.2: the kill-switch submits; it never touches SQLite itself.

    If the switch were committing synchronously on the calling thread, the write would
    land on this thread and the worker would have nothing to do.
    """
    import threading

    writer = _writer(tmp_path)
    writer.start()
    switch = KillSwitch(config=CONFIG, sink=JournalSink(writer))

    switch.record_realized_r(Decimal("-3"), THURSDAY)
    assert writer.drain()

    assert writer.committed_events == 1
    assert writer.writer_thread_id != threading.current_thread().ident
    writer.stop()
