"""Immutable positions and their transitions.

A position is *open* until its quantity reaches zero. Adding to it blends the average entry
price; reducing it realizes PnL on the reduced slice only and leaves the surviving slice at
its original entry. Closing realizes whatever remains.

Keeping the object immutable means "the position as it was when bar N printed" is still
available, which is exactly what the journal needs to make a draw-down replay reproducible.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from engine.domain.orders import OrderSide

if TYPE_CHECKING:
    pass

__all__ = [
    "Position",
    "PositionSide",
    "PositionStateError",
    "average_entry_price",
    "close_position",
    "increase_position",
    "reduce_position",
    "unrealized_pnl",
]


class PositionStateError(RuntimeError):
    """An illegal position transition was attempted."""


class PositionSide(StrEnum):
    """Direction of the open position."""

    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def opposite(self) -> PositionSide:
        return PositionSide.SHORT if self is PositionSide.LONG else PositionSide.LONG

    @property
    def sign(self) -> Decimal:
        """Multiplier that turns a price move into this side's PnL."""
        return Decimal(1) if self is PositionSide.LONG else Decimal(-1)

    @classmethod
    def from_order_side(cls, side: OrderSide) -> PositionSide:
        """Map an order side to the position it opens."""
        if side is OrderSide.BUY:
            return cls.LONG
        if side is OrderSide.SELL:
            return cls.SHORT
        raise PositionStateError(f"not an order side: {side!r}")


@dataclass(frozen=True, slots=True)
class Position:
    """An open (or closed) position in one instrument."""

    position_id: str
    run_id: str
    instrument: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    entry_time: dt.datetime
    exit_price: Decimal | None = None
    exit_time: dt.datetime | None = None
    realized_pnl: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        for name in ("position_id", "run_id", "instrument"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string, got {value!r}")

        if not isinstance(self.side, PositionSide):
            raise ValueError(
                f"side must be a PositionSide, got {type(self.side).__name__}"
            )
        _require_decimal(self.quantity, field="quantity", non_negative=True)
        if self.quantity > 0:
            _require_decimal(self.entry_price, field="entry_price", positive=True)
        if not isinstance(self.entry_time, dt.datetime) or self.entry_time.tzinfo is None:
            raise ValueError(
                f"entry_time must be timezone-aware, got {self.entry_time!r}"
            )
        for name in ("exit_price", "exit_time"):
            value = getattr(self, name)
            if value is None:
                continue
            if name == "exit_price":
                _require_decimal(value, field=name, positive=True)
            elif value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware, got {value!r}")

    def __hash__(self) -> int:
        """Hash on identity.

        Derived from the position id rather than the builtin ``hash()``, which is salted per
        process and would give a different value after a restart.
        """
        digest = hashlib.sha256(
            self.position_id.encode("utf-8") + self.instrument.encode("utf-8")
        ).digest()
        return int.from_bytes(digest[:8], "big")

    # -- derived -------------------------------------------------------------- #
    @property
    def is_open(self) -> bool:
        return self.quantity > 0

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0

    @property
    def is_long(self) -> bool:
        return self.side is PositionSide.LONG

    @property
    def notional(self) -> Decimal:
        """Quantity times entry price, in quote currency. Unsigned by design."""
        return self.quantity * self.entry_price


def average_entry_price(
    existing_quantity: Decimal,
    existing_price: Decimal,
    added_quantity: Decimal,
    added_price: Decimal,
) -> Decimal:
    """Volume-weighted average of two tranches.

    The result is exact: both inputs are Decimals and the only division is by a quantity
    that is guaranteed to be positive, so no rounding context is involved.
    """
    _require_decimal(added_quantity, field="added_quantity", positive=True)
    _require_decimal(existing_quantity, field="existing_quantity", non_negative=True)
    _require_decimal(existing_price, field="existing_price", positive=True)
    _require_decimal(added_price, field="added_price", positive=True)

    total = existing_quantity + added_quantity
    if total == 0:
        raise ValueError("cannot blend two zero-quantity tranches")
    return (existing_quantity * existing_price + added_quantity * added_price) / total


def unrealized_pnl(position: Position, current_price: Decimal) -> Decimal:
    """Mark-to-market PnL of ``position`` at ``current_price``.

    Uses the mid price: a strategy reading mid bars must see the same PnL the books will
    eventually realize, up to spread.
    """
    _require_decimal(current_price, field="current_price", positive=True)
    if not position.is_open:
        return Decimal(0)
    return position.side.sign * (current_price - position.entry_price) * position.quantity


def increase_position(
    position: Position, quantity: Decimal, price: Decimal, at: dt.datetime
) -> Position:
    """Add to an open position, blending the entry price.

    Called *increase* rather than *add* because the quantity grows and the average entry
    moves; calling it ``add`` invites the caller to think the original entry is preserved.
    """
    _require_open(position)
    _require_decimal(quantity, field="quantity", positive=True)
    _require_decimal(price, field="price", positive=True)
    if at.tzinfo is None:
        raise ValueError(f"at must be timezone-aware, got {at!r}")

    blended = average_entry_price(
        position.quantity, position.entry_price, quantity, price
    )
    return dataclasses.replace(
        position, quantity=position.quantity + quantity, entry_price=blended
    )


def reduce_position(
    position: Position, quantity: Decimal, price: Decimal, at: dt.datetime
) -> Position:
    """Remove ``quantity`` from an open position, realizing its PnL.

    The surviving slice keeps the original entry price, which is what makes a scale-out
    auditable: each slice's cost basis is the price it was actually opened at.
    """
    _require_open(position)
    _require_decimal(quantity, field="quantity", positive=True)
    _require_decimal(price, field="price", positive=True)
    if at.tzinfo is None:
        raise ValueError(f"at must be timezone-aware, got {at!r}")
    if quantity > position.quantity:
        raise PositionStateError(
            f"cannot reduce {quantity} from a position holding {position.quantity}"
        )

    realized = position.side.sign * (price - position.entry_price) * quantity
    remaining = position.quantity - quantity
    return dataclasses.replace(
        position,
        quantity=remaining,
        realized_pnl=position.realized_pnl + realized,
        exit_price=price if remaining == 0 else position.exit_price,
        exit_time=at if remaining == 0 else position.exit_time,
    )


def close_position(
    position: Position, quantity: Decimal, price: Decimal, at: dt.datetime
) -> Position:
    """Fully or partially close ``position`` at ``price``.

    A partial close leaves the position open; a full one marks it flat. Both paths record
    the exit, so a reader can never confuse "fully closed" with "still holding a stub".
    """
    _require_open(position)
    if at is None or at.tzinfo is None:
        raise ValueError(f"at must be a timezone-aware datetime, got {at!r}")
    return reduce_position(position, quantity, price, at)


def _require_open(position: Position) -> None:
    if not position.is_open:
        raise PositionStateError(
            f"position {position.position_id} is closed and cannot be modified"
        )


def _require_decimal(
    value: Any, *, field: str, positive: bool = False, non_negative: bool = False
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError(f"{field} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite, got {value!r}")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive, got {value!r}")
    if non_negative and value < 0:
        raise ValueError(f"{field} must not be negative, got {value!r}")
    return value
