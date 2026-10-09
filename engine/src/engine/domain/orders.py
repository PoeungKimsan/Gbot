"""Immutable orders, with state transitions that never mutate.

An order is created once, acknowledged by the venue, filled some number of times, and then
reaches a terminal state. Every step returns a new object. That is not decoration: the
journalled event for a transition is derived from the transition itself, so an order that
mutated in place would leave no record of what it looked like before.

Validation lives on :class:`OrderRequest`, because that is the last point where a malformed
order can be refused before it enters the simulation.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = [
    "Order",
    "OrderKind",
    "OrderRequest",
    "OrderSide",
    "OrderStateError",
    "OrderStatus",
    "transition",
]


class OrderStateError(RuntimeError):
    """An illegal order state transition was attempted."""


class OrderSide(StrEnum):
    """Direction of the order."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> OrderSide:
        """The other side.

        Referenced through the class rather than by bare name: inside an ``Enum`` body a
        sibling member is not in scope the way a plain class attribute would be.
        """
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY

    @classmethod
    def from_value(cls, value: Any) -> OrderSide | None:
        if not isinstance(value, str):
            return None
        try:
            return cls(value.strip().upper())
        except ValueError:
            return None


class OrderKind(StrEnum):
    """Execution style."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"

    @classmethod
    def from_value(cls, value: Any) -> OrderKind | None:
        if not isinstance(value, str):
            return None
        try:
            return cls(value.strip().upper())
        except ValueError:
            return None


class OrderStatus(StrEnum):
    """Lifecycle state."""

    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        """Whether this order can never change again.

        Members are referenced through the class rather than by bare name: inside an
        ``Enum`` body a sibling member is not in scope the way a plain class attribute is,
        so ``OrderStatus.FILLED`` and not ``FILLED``.
        """
        return self in {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }

    @property
    def is_open(self) -> bool:
        """Whether this order can still be modified or filled."""
        return self in {
            OrderStatus.PENDING,
            OrderStatus.ACKNOWLEDGED,
            OrderStatus.PARTIALLY_FILLED,
        }

    @classmethod
    def from_value(cls, value: Any) -> OrderStatus | None:
        if not isinstance(value, str):
            return None
        try:
            return cls(value.strip().upper())
        except ValueError:
            return None


#: Statuses from which an order can no longer change.
_TERMINAL = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """Everything the venue needs, validated once.

    A limit order carries ``limit_price`` and no ``stop_price``; a stop order the reverse;
    a market order neither. Mixing them is refused here rather than being reinterpreted
    downstream, where it would silently change the fill semantics.
    """

    instrument: str
    side: OrderSide
    kind: OrderKind
    quantity: Decimal
    submitted_at: dt.datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    tag: str = ""
    parent_order_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError(
                f"instrument must be a non-empty string, got {self.instrument!r}"
            )
        if not isinstance(self.side, OrderSide):
            raise ValueError(f"side must be an OrderSide, got {type(self.side).__name__}")
        if not isinstance(self.kind, OrderKind):
            raise ValueError(f"kind must be an OrderKind, got {type(self.kind).__name__}")

        _require_decimal(self.quantity, field="quantity", positive=True)
        if self.submitted_at.tzinfo is None:
            raise ValueError(
                f"submitted_at must be timezone-aware, got {self.submitted_at!r}"
            )

        if self.kind is OrderKind.LIMIT:
            if self.limit_price is None:
                raise ValueError("a LIMIT order requires a limit_price")
            if self.stop_price is not None:
                raise ValueError("a LIMIT order must not carry a stop_price")
            _require_decimal(self.limit_price, field="limit_price", positive=True)
        elif self.kind is OrderKind.STOP:
            if self.stop_price is None:
                raise ValueError("a STOP order requires a stop_price")
            if self.limit_price is not None:
                raise ValueError("a STOP order must not carry a limit_price")
            _require_decimal(self.stop_price, field="stop_price", positive=True)
        else:
            if self.limit_price is not None:
                raise ValueError("a MARKET order must not carry a limit_price")
            if self.stop_price is not None:
                raise ValueError("a MARKET order must not carry a stop_price")

    @property
    def trigger_price(self) -> Decimal | None:
        """The price at which this order becomes live, if it has one."""
        return self.limit_price if self.kind is OrderKind.LIMIT else self.stop_price


@dataclass(frozen=True, slots=True)
class Order:
    """An order and everything that has happened to it so far.

    Frozen. :meth:`record_fill`, :meth:`cancel` and :meth:`reject` all return a new
    instance, so an order's history is the sequence of objects, not the final object.
    """

    order_id: str
    run_id: str
    request: OrderRequest
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = Decimal(0)
    notional: Decimal = Decimal(0)
    fills: tuple[tuple[Decimal, Decimal, dt.datetime], ...] = ()
    acknowledged_at: dt.datetime | None = None
    cancelled_at: dt.datetime | None = None
    rejected_at: dt.datetime | None = None
    rejection_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError(
                f"order_id must be a non-empty string, got {self.order_id!r}"
            )
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError(f"run_id must be a non-empty string, got {self.run_id!r}")
        if not isinstance(self.request, OrderRequest):
            raise ValueError(
                f"request must be an OrderRequest, got {type(self.request).__name__}"
            )
        if not isinstance(self.status, OrderStatus):
            raise ValueError(
                f"status must be an OrderStatus, got {type(self.status).__name__}"
            )
        _require_decimal(self.filled_quantity, field="filled_quantity", non_negative=True)
        if self.filled_quantity > self.request.quantity:
            raise ValueError(
                f"filled {self.filled_quantity} exceeds requested {self.request.quantity}"
            )

    def __hash__(self) -> int:
        """Hash on identity so orders can key dicts despite the nested request.

        Derived from the order's own id rather than the builtin ``hash()``, which is salted
        per process and would not survive a restart.
        """
        digest = hashlib.sha256(self.order_id.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big")

    # -- derived -------------------------------------------------------------- #
    @property
    def remaining_quantity(self) -> Decimal:
        return self.request.quantity - self.filled_quantity

    @property
    def is_complete(self) -> bool:
        return self.remaining_quantity == 0

    @property
    def average_fill_price(self) -> Decimal | None:
        """Volume-weighted average fill price, or ``None`` when unfilled.

        Accumulated as notional rather than by recomputing over ``fills``, so a long series
        of partials cannot drift.
        """
        if self.filled_quantity == 0:
            return None
        return self.notional / self.filled_quantity

    @property
    def side(self) -> OrderSide:
        return self.request.side

    @property
    def kind(self) -> OrderKind:
        return self.request.kind

    @property
    def instrument(self) -> str:
        return self.request.instrument

    # -- transitions ---------------------------------------------------------- #
    def _replace(self, **changes: Any) -> Order:
        return dataclasses.replace(self, **changes)

    def record_fill(
        self, quantity: Decimal, price: Decimal, at: dt.datetime
    ) -> Order:
        """Record one fill. Returns a new order, never mutates ``self``."""
        if self.status is OrderStatus.PENDING:
            raise OrderStateError(
                f"order {self.order_id} must be ACKNOWLEDGED before it can fill"
            )
        if self.status in _TERMINAL:
            raise OrderStateError(
                f"order {self.order_id} is terminal ({self.status.value}) and cannot fill"
            )
        if at.tzinfo is None:
            raise ValueError(f"fill time must be timezone-aware, got {at!r}")
        _require_decimal(quantity, field="quantity", positive=True)
        _require_decimal(price, field="price", positive=True)

        if quantity > self.remaining_quantity:
            raise OrderStateError(
                f"fill of {quantity} exceeds remaining {self.remaining_quantity} "
                f"for order {self.order_id}"
            )

        notional = self.notional + (price * quantity)
        filled = self.filled_quantity + quantity
        status = (
            OrderStatus.FILLED
            if filled == self.request.quantity
            else OrderStatus.PARTIALLY_FILLED
        )

        return self._replace(
            status=status,
            filled_quantity=filled,
            notional=notional,
            fills=(*self.fills, (quantity, price, at)),
        )

    def cancel(self, at: dt.datetime) -> Order:
        """Cancel an open order that has reached the venue."""
        if self.status is OrderStatus.PENDING:
            raise OrderStateError(
                f"order {self.order_id} is PENDING and was never sent to the venue"
            )
        if self.status in _TERMINAL:
            raise OrderStateError(
                f"order {self.order_id} is terminal ({self.status.value}) and cannot cancel"
            )
        if at.tzinfo is None:
            raise ValueError(f"cancellation time must be timezone-aware, got {at!r}")
        return self._replace(status=OrderStatus.CANCELLED, cancelled_at=at)

    def reject(self, reason: str, at: dt.datetime) -> Order:
        """Reject the order, recording why."""
        if self.status in _TERMINAL:
            raise OrderStateError(
                f"order {self.order_id} is terminal ({self.status.value}) and cannot reject"
            )
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"rejection reason must be non-empty, got {reason!r}")
        if at.tzinfo is None:
            raise ValueError(f"rejection time must be timezone-aware, got {at!r}")
        return self._replace(
            status=OrderStatus.REJECTED,
            rejected_at=at,
            rejection_reason=reason,
        )


def transition(order: Order, status: OrderStatus, *, at: dt.datetime | None = None) -> Order:
    """Move an order to ``status`` using the order's own transition rules."""
    if not isinstance(status, OrderStatus):
        raise OrderStateError(f"{status!r} is not an OrderStatus")

    match status:
        case OrderStatus.ACKNOWLEDGED:
            if order.status is not OrderStatus.PENDING:
                raise OrderStateError(
                    f"order {order.order_id} cannot be acknowledged from "
                    f"{order.status.value}"
                )
            if at is None:
                raise ValueError("acknowledgement requires a timezone-aware 'at'")
            if at.tzinfo is None:
                raise ValueError(f"'at' must be timezone-aware, got {at!r}")
            return order._replace(status=OrderStatus.ACKNOWLEDGED, acknowledged_at=at)
        case OrderStatus.CANCELLED:
            if at is None:
                raise ValueError("cancellation requires a timezone-aware 'at'")
            return order.cancel(at)
        case OrderStatus.REJECTED:
            raise OrderStateError("use Order.reject() so the reason is recorded")
        case _:
            raise OrderStateError(
                f"cannot transition order {order.order_id} to {status.value} directly"
            )


def _require_decimal(
    value: Any, *, field: str, positive: bool = False, non_negative: bool = False
) -> Decimal:
    """Assert ``value`` is a usable Decimal, rejecting floats and non-finite values."""
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError(f"{field} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite, got {value!r}")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive, got {value!r}")
    if non_negative and value < 0:
        raise ValueError(f"{field} must not be negative, got {value!r}")
    return value
