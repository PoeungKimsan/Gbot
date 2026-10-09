"""The latching kill-switch.

One rule, and everything here exists to serve it: **once the day's loss reaches the
limit, the engine stops entering until the next trading day, and nothing short of
that rollover clears it.**

Three properties make that rule real rather than aspirational:

* **The latch holds.** A profitable trade, a caller who would prefer it did not, or
  a restart cannot clear it. The only transition out of ``TRIPPED`` is the trading
  day rolling over.
* **The latch survives a restart.** Every transition is submitted to the journal as
  a ``RISK_KILLSWITCH`` event, so a process that dies mid-day comes back up tripped.
  An in-memory latch would let the engine re-enter a market it already gave up on
  for the day. Because the event is an ordinary journalled fact, the restore path is
  a replay of the same events a reader would verify.
* **Tripping blocks entries and cancels resting limits.** A resting limit becomes a
  market order the moment price reaches it, so leaving it in the book is leaving an
  entry armed. Exits are always allowed: blocking an exit is how a stop becomes a
  blow-up.

The submit-only rule from AGENTS.md 2.2 is respected by construction: this module
never opens a database. It submits :class:`~engine.domain.events.DomainEvent` bytes
to an injected sink, and restores from events someone else read out of the journal.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol

from engine.domain.events import DomainEvent, EventType
from engine.domain.orders import Order, OrderStatus
from engine.risk.config import RiskConfig
from engine.risk.drawdown import DailyPnLTracker, trading_day_for
from engine.risk.sizing import r_multiple

__all__ = [
    "EventSink",
    "KillSwitch",
    "KillSwitchState",
    "OrderRole",
    "RiskAction",
    "RiskDecision",
]

#: Event type every kill-switch transition is journalled under. Defined in
#: :mod:`engine.domain.events` so a reader can find them without importing this module.
_EVENT_TYPE: Final[EventType] = EventType.RISK_KILLSWITCH


class KillSwitchState(StrEnum):
    """Whether the engine may open positions."""

    ARMED = "ARMED"
    TRIPPED = "TRIPPED"


class OrderRole(StrEnum):
    """What an order is for, which is what decides whether it may proceed."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"


class RiskAction(StrEnum):
    """What a caller must do with an order the switch has considered."""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    CANCEL = "CANCEL"


@dataclasses.dataclass(frozen=True, slots=True)
class RiskDecision:
    """The verdict on one order, with enough context to act and to audit."""

    action: RiskAction
    reason: str
    order_id: str
    state: KillSwitchState

    @property
    def permitted(self) -> bool:
        """Whether the order may proceed as it stands."""
        return self.action is RiskAction.ALLOW


class EventSink(Protocol):
    """Where kill-switch transitions are submitted. The journal writer satisfies it."""

    def submit(self, event: DomainEvent) -> None:  # pragma: no cover - protocol
        """Accept one event without blocking, or raise."""


class KillSwitch:
    """Latches on the daily loss limit and judges orders against the latch.

    Args:
        config: The risk policy. ``daily_loss_limit_r`` is the ceiling.
        sink: Where transitions are journalled. Optional, so the switch can be
            exercised without a journal; production always passes one.
        tracker: PnL accumulator. A fresh one is created per day of use.
    """

    def __init__(
        self,
        *,
        config: RiskConfig,
        sink: EventSink | None = None,
        tracker: DailyPnLTracker | None = None,
    ) -> None:
        self._config = config
        self._sink = sink
        self._tracker = tracker if tracker is not None else DailyPnLTracker()
        self._state: KillSwitchState = KillSwitchState.ARMED
        self._tripped_day: dt.date | None = None
        self._tripped_at: dt.datetime | None = None
        self._loss_r: Decimal = Decimal(0)
        self._trip_reason: str = ""

    # -- introspection ------------------------------------------------------- #
    @property
    def state(self) -> KillSwitchState:
        """``ARMED`` or ``TRIPPED``."""
        return self._state

    @property
    def is_tripped(self) -> bool:
        return self._state is KillSwitchState.TRIPPED

    @property
    def trading_day(self) -> dt.date | None:
        """The trading day the latch was set for, or ``None`` when armed."""
        return self._tripped_day

    @property
    def loss_r(self) -> Decimal:
        """The current trading day's total in units of R."""
        return self._loss_r

    @property
    def trip_reason(self) -> str:
        """Why the latch was set, or empty when armed."""
        return self._trip_reason

    @property
    def tripped_at(self) -> dt.datetime | None:
        """When the latch was set, or ``None`` when armed."""
        return self._tripped_at

    @property
    def tracker(self) -> DailyPnLTracker:
        """The PnL accumulator backing the daily totals."""
        return self._tracker

    # -- recording results --------------------------------------------------- #
    def record_realized_r(self, r_multiple: Decimal, at: dt.datetime) -> KillSwitchState:
        """Record a realized result expressed in R.

        Args:
            r_multiple: The trade's R multiple; negative for a loss.
            at: When it was realized.

        Returns:
            The state after recording, so a caller can react to a trip immediately.
        """
        self._roll_day(at)
        self._tracker.record_r(r_multiple, at)
        return self._reconsider(at)

    def record_trade(
        self, pnl: Decimal, *, risk_amount: Decimal, at: dt.datetime
    ) -> KillSwitchState:
        """Record a realized result in currency, converting it to R.

        Args:
            pnl: Realized profit or loss.
            risk_amount: The amount that was risked on the trade.
            at: When it was realized.

        Returns:
            The state after recording.

        Raises:
            SizingError: if the PnL or the risked amount is unusable.
        """
        multiple = r_multiple(pnl, risk_amount)
        self._roll_day(at)
        self._tracker.record_realized(pnl, at, r_multiple=multiple)
        return self._reconsider(at)

    # -- the latch ----------------------------------------------------------- #
    def evaluate(self, at: dt.datetime) -> KillSwitchState:
        """Reconcile the latch with the trading day ``at`` falls in.

        Idempotent: calling it repeatedly within a day changes nothing. Call it on
        every tick, and once at startup after a restore.

        Args:
            at: The current instant.

        Returns:
            The state after reconciliation.
        """
        return self._roll_day(at)

    def reset(self, at: dt.datetime) -> KillSwitchState:
        """Clear the latch if and only if the trading day has rolled over.

        Deliberately not a forced reset: an explicit request to re-enable trading
        mid-loss is exactly what a risk layer must refuse.
        """
        return self._roll_day(at)

    # -- judging orders ------------------------------------------------------ #
    def adjudicate(self, order: Order, role: OrderRole, at: dt.datetime) -> RiskDecision:
        """Decide what must happen to ``order`` given the latch.

        Args:
            order: The order under consideration.
            role: Whether it opens risk or closes it.
            at: The current instant.

        Returns:
            A :class:`RiskDecision`.

        Raises:
            TypeError: if the order or the role is of the wrong type.
        """
        if not isinstance(order, Order):
            raise TypeError(f"order must be an Order, got {type(order).__name__}")
        if not isinstance(role, OrderRole):
            raise TypeError(f"role must be an OrderRole, got {type(role).__name__}")

        self._roll_day(at)

        if self._state is KillSwitchState.ARMED:
            return RiskDecision(
                action=RiskAction.ALLOW,
                reason="kill-switch is armed for the trading day",
                order_id=order.order_id,
                state=self._state,
            )

        # An exit always proceeds. Blocking an exit is how a stop becomes a blow-up.
        if role is OrderRole.EXIT:
            return RiskDecision(
                action=RiskAction.ALLOW,
                reason="exits proceed while the kill-switch is tripped",
                order_id=order.order_id,
                state=self._state,
            )

        if order.status is OrderStatus.PENDING:
            return RiskDecision(
                action=RiskAction.BLOCK,
                reason=(
                    f"entry refused: {self._trip_reason}; resting orders are "
                    f"cancelled until the next trading day"
                ),
                order_id=order.order_id,
                state=self._state,
            )

        if order.status.is_open:
            return RiskDecision(
                action=RiskAction.CANCEL,
                reason=(
                    f"resting entry cancelled: {self._trip_reason}; waiting for a "
                    f"fill is waiting to enter"
                ),
                order_id=order.order_id,
                state=self._state,
            )

        return RiskDecision(
            action=RiskAction.ALLOW,
            reason=f"order is already {order.status.value}; nothing to block",
            order_id=order.order_id,
            state=self._state,
        )

    # -- internals ----------------------------------------------------------- #
    @classmethod
    def restore(
        cls,
        events: object,
        *,
        config: RiskConfig,
        at: dt.datetime,
        sink: EventSink | None = None,
        tracker: DailyPnLTracker | None = None,
    ) -> KillSwitch:
        """Rebuild a switch from journalled transitions.

        The fold is over journal order, which is the order a reader gets. The final
        state is then reconciled against the trading day the process is starting in:
        a latch set for a day that has since ended is cleared, because that is what
        would have happened had the process stayed up.

        Args:
            events: The journalled events, in order. Non-kill-switch events are
                ignored, so a caller may pass every event from the journal.
            config: The risk policy.
            at: The current instant, which decides whether the latch still applies.
            sink: Where future transitions are journalled.
            tracker: PnL accumulator to adopt. ``None`` leaves the day's loss
                unrecorded on this side, which is the honest state after a restart:
                the totals for the elapsed day were, and still are, journalled.

        Returns:
            A :class:`KillSwitch` in the restored state.

        Raises:
            TypeError: if ``events`` is not iterable.
        """
        if isinstance(events, (str, bytes)) or not hasattr(events, "__iter__"):
            raise TypeError(f"events must be iterable, got {type(events).__name__}")

        switch = cls(config=config, sink=sink, tracker=tracker)

        latched_day: dt.date | None = None
        for event in events:
            if not isinstance(event, DomainEvent):
                continue
            if event.event_type is not _EVENT_TYPE:
                continue

            payload = event.payload
            state = str(payload.get("state", "")).upper()
            raw_day = payload.get("trading_day")
            if not isinstance(raw_day, str) or not raw_day.strip():
                continue
            try:
                journalled_day = dt.date.fromisoformat(raw_day)
            except ValueError:
                # A malformed row is not evidence either way; keep the last good state.
                continue

            if state == KillSwitchState.TRIPPED.value:
                latched_day = journalled_day
                switch._state = KillSwitchState.TRIPPED
                switch._tripped_day = journalled_day
                switch._tripped_at = event.occurred_at
                switch._trip_reason = str(payload.get("reason", ""))
                loss = payload.get("loss_r")
                try:
                    switch._loss_r = Decimal(str(loss))
                except ArithmeticError:
                    switch._loss_r = Decimal(0)
            elif state == KillSwitchState.ARMED.value:
                latched_day = None
                switch._state = KillSwitchState.ARMED
                switch._tripped_day = None
                switch._tripped_at = None
                switch._trip_reason = ""
                switch._loss_r = Decimal(0)

        if latched_day is not None and latched_day != trading_day_for(at):
            # The day ended while the process was down, and the rollover is the only
            # thing that clears the latch - so it has already happened.
            switch._roll_day(at)

        return switch

    def _roll_day(self, at: dt.datetime) -> KillSwitchState:
        """Re-arm if the trading day has moved on. The only exit from the latch."""
        if self._state is KillSwitchState.TRIPPED:
            day = trading_day_for(at)
            if self._tripped_day != day:
                previous = self._tripped_day
                self._state = KillSwitchState.ARMED
                self._tripped_day = None
                self._tripped_at = None
                self._trip_reason = ""
                self._loss_r = self._tracker.current(at).total_r
                self._emit(
                    KillSwitchState.ARMED,
                    day,
                    at,
                    reason=(
                        f"trading day {previous} ended; the latch cleared at "
                        f"{day.isoformat()}"
                    ),
                )
        return self._state

    def _reconsider(self, at: dt.datetime) -> KillSwitchState:
        """Update the running loss and trip if the limit is reached."""
        self._loss_r = self._tracker.current(at).total_r
        if (
            self._state is KillSwitchState.ARMED
            and self._loss_r <= -self._config.daily_loss_limit_r
        ):
            self._trip(at)
        return self._state

    def _trip(self, at: dt.datetime) -> None:
        day = trading_day_for(at)
        self._state = KillSwitchState.TRIPPED
        self._tripped_day = day
        self._tripped_at = at
        self._trip_reason = (
            f"daily loss reached {self._loss_r}R, the "
            f"{self._config.daily_loss_limit_r}R limit"
        )
        self._emit(KillSwitchState.TRIPPED, day, at, reason=self._trip_reason)

    def _emit(
        self, state: KillSwitchState, day: dt.date, at: dt.datetime, *, reason: str
    ) -> None:
        """Submit the transition to the journal, if a sink is attached."""
        if self._sink is None:
            return
        self._sink.submit(
            DomainEvent(
                event_id=f"risk-killswitch-{state.value.lower()}-{uuid.uuid4().hex}",
                event_type=_EVENT_TYPE,
                occurred_at=at,
                payload={
                    "state": state.value,
                    "trading_day": day.isoformat(),
                    "reason": reason,
                    "loss_r": str(self._loss_r),
                    "limit_r": str(self._config.daily_loss_limit_r),
                },
            )
        )

