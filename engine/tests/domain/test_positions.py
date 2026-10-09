"""Unit tests for :mod:`engine.domain.positions`.

Positions are immutable. Opening, adding to and closing a position all return new objects,
which is what makes "reduced" a first-class transition rather than a mutation that is hard to
audit after the fact.
"""

import datetime as dt
from decimal import Decimal

import pytest

from engine.domain.orders import OrderSide
from engine.domain.positions import (
    Position,
    PositionSide,
    PositionStateError,
    average_entry_price,
    close_position,
    increase_position,
    reduce_position,
    unrealized_pnl,
)

UTC = dt.UTC
AT = dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def _position(**overrides: object) -> Position:
    defaults: dict[str, object] = {
        "position_id": "pos-1",
        "run_id": "run-1",
        "instrument": "XAU_USD",
        "side": PositionSide.LONG,
        "quantity": Decimal("1"),
        "entry_price": Decimal("2038.25"),
        "entry_time": AT,
    }
    defaults.update(overrides)
    return Position(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# enums
# --------------------------------------------------------------------------- #
def test_position_side_wire_values() -> None:
    assert {s.value for s in PositionSide} == {"LONG", "SHORT"}


def test_position_side_from_order_side() -> None:
    assert PositionSide.from_order_side(OrderSide.BUY) is PositionSide.LONG
    assert PositionSide.from_order_side(OrderSide.SELL) is PositionSide.SHORT


def test_position_side_opposite() -> None:
    assert PositionSide.LONG.opposite is PositionSide.SHORT
    assert PositionSide.SHORT.opposite is PositionSide.LONG


def test_position_side_sign() -> None:
    """The sign drives the PnL formula: long gains as price rises."""
    assert PositionSide.LONG.sign == Decimal(1)
    assert PositionSide.SHORT.sign == Decimal(-1)


# --------------------------------------------------------------------------- #
# construction
# --------------------------------------------------------------------------- #
def test_position_starts_open() -> None:
    position = _position()

    assert position.is_open is True
    assert position.is_flat is False
    assert position.realized_pnl == Decimal(0)


def test_position_is_frozen() -> None:
    position = _position()

    with pytest.raises(Exception):  # noqa: B017
        position.quantity = Decimal("2")  # type: ignore[misc]


@pytest.mark.parametrize("bad", [Decimal("-1"), Decimal("NaN"), 1.5])
def test_position_rejects_invalid_quantity(bad: object) -> None:
    with pytest.raises(ValueError, match="quantity"):
        _position(quantity=bad)


def test_position_accepts_a_zero_quantity() -> None:
    """Zero is how a fully-closed position looks, so it must be constructible."""
    closed = _position(quantity=Decimal("0"))

    assert closed.is_flat is True
    assert closed.is_open is False


def test_position_rejects_naive_entry_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _position(entry_time=dt.datetime(2026, 1, 15, 14, 30))


def test_position_rejects_non_positive_entry_price() -> None:
    with pytest.raises(ValueError, match="entry_price"):
        _position(entry_price=Decimal("0"))


# --------------------------------------------------------------------------- #
# unrealized PnL
# --------------------------------------------------------------------------- #
def test_long_unrealized_pnl_rises_with_price() -> None:
    position = _position(
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("2038.00"),
    )

    assert unrealized_pnl(position, Decimal("2038.50")) == Decimal("0.50")
    assert unrealized_pnl(position, Decimal("2037.50")) == Decimal("-0.50")


def test_short_unrealized_pnl_falls_as_price_rises() -> None:
    position = _position(
        side=PositionSide.SHORT,
        quantity=Decimal("1"),
        entry_price=Decimal("2038.00"),
    )

    assert unrealized_pnl(position, Decimal("2038.50")) == Decimal("-0.50")
    assert unrealized_pnl(position, Decimal("2037.50")) == Decimal("0.50")


def test_unrealized_pnl_scales_with_quantity() -> None:
    position = _position(
        side=PositionSide.LONG,
        quantity=Decimal("3"),
        entry_price=Decimal("2038.00"),
    )

    assert unrealized_pnl(position, Decimal("2039.00")) == Decimal("3")


def test_unrealized_pnl_at_entry_is_zero() -> None:
    position = _position()

    assert unrealized_pnl(position, position.entry_price) == Decimal(0)


def test_unrealized_pnl_returns_decimal() -> None:
    position = _position()

    assert isinstance(unrealized_pnl(position, Decimal("2039.00")), Decimal)


# --------------------------------------------------------------------------- #
# average entry price
# --------------------------------------------------------------------------- #
def test_average_entry_price_weights_by_quantity() -> None:
    blended = average_entry_price(
        Decimal("1"), Decimal("2038.00"), Decimal("1"), Decimal("2040.00")
    )

    assert blended == Decimal("2039")


def test_average_entry_price_with_an_empty_existing_tranche() -> None:
    """Blending into nothing is just the added price, not an error."""
    assert average_entry_price(
        Decimal("0"), Decimal("2038.00"), Decimal("1"), Decimal("2039.00")
    ) == Decimal("2039")


def test_average_entry_price_rejects_two_empty_tranches() -> None:
    with pytest.raises(ValueError, match="quantity"):
        average_entry_price(Decimal("0"), Decimal("2038.00"), Decimal("0"), Decimal("2039.00"))


# --------------------------------------------------------------------------- #
# transitions
# --------------------------------------------------------------------------- #
def test_increase_blends_the_entry_price() -> None:
    position = _position(quantity=Decimal("1"), entry_price=Decimal("2038.00"))

    increased = increase_position(
        position, Decimal("1"), Decimal("2040.00"), AT.replace(minute=31)
    )

    assert increased.quantity == Decimal("2")
    assert increased.entry_price == Decimal("2039")
    assert position.quantity == Decimal("1")


def test_increase_on_a_closed_position_is_rejected() -> None:
    position = close_position(
        _position(), Decimal("1"), Decimal("2038.30"), AT.replace(minute=31)
    )

    with pytest.raises(PositionStateError, match="closed"):
        increase_position(position, Decimal("1"), Decimal("2038.00"), AT.replace(minute=32))


def test_reduce_keeps_the_entry_price_and_returns_realized_pnl() -> None:
    """A partial reduction does not reprice the surviving position."""
    position = _position(
        side=PositionSide.LONG,
        quantity=Decimal("2"),
        entry_price=Decimal("2038.00"),
    )

    reduced = reduce_position(
        position, Decimal("1"), Decimal("2039.00"), AT.replace(minute=31)
    )

    assert reduced.quantity == Decimal("1")
    assert reduced.entry_price == Decimal("2038.00")
    assert reduced.realized_pnl == Decimal("1")


def test_reduce_by_more_than_held_is_rejected() -> None:
    position = _position(quantity=Decimal("1"))

    with pytest.raises(PositionStateError, match="cannot reduce"):
        reduce_position(position, Decimal("2"), Decimal("2039.00"), AT.replace(minute=31))


def test_full_reduction_closes_the_position() -> None:
    position = _position(quantity=Decimal("1"), entry_price=Decimal("2038.00"))

    closed = reduce_position(position, Decimal("1"), Decimal("2039.00"), AT.replace(minute=31))

    assert closed.is_open is False
    assert closed.is_flat is True
    assert closed.quantity == Decimal(0)
    assert closed.realized_pnl == Decimal("1")


def test_close_realizes_pnl_for_a_long() -> None:
    position = _position(
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("2038.00"),
    )

    closed = close_position(position, Decimal("1"), Decimal("2039.50"), AT.replace(minute=31))

    assert closed.is_open is False
    assert closed.realized_pnl == Decimal("1.50")
    assert closed.exit_price == Decimal("2039.50")
    assert closed.exit_time == AT.replace(minute=31)


def test_close_realizes_pnl_for_a_short() -> None:
    position = _position(
        side=PositionSide.SHORT,
        quantity=Decimal("1"),
        entry_price=Decimal("2038.00"),
    )

    closed = close_position(position, Decimal("1"), Decimal("2037.25"), AT.replace(minute=31))

    assert closed.realized_pnl == Decimal("0.75")


def test_close_beyond_the_held_quantity_is_rejected() -> None:
    position = _position(quantity=Decimal("1"))

    with pytest.raises(PositionStateError, match="cannot reduce"):
        close_position(position, Decimal("1.5"), Decimal("2039.00"), AT.replace(minute=31))


def test_close_an_already_closed_position_is_rejected() -> None:
    position = close_position(
        _position(), Decimal("1"), Decimal("2038.30"), AT.replace(minute=31)
    )

    with pytest.raises(PositionStateError, match="closed"):
        close_position(position, Decimal("1"), Decimal("2038.40"), AT.replace(minute=32))


def test_close_below_the_held_quantity_leaves_a_stub_open() -> None:
    """A partial close keeps the position open rather than closing it."""
    position = _position(quantity=Decimal("2"), entry_price=Decimal("2038.00"))

    closed = close_position(position, Decimal("1"), Decimal("2039.00"), AT.replace(minute=31))

    assert closed.is_open is True
    assert closed.quantity == Decimal("1")
    assert closed.realized_pnl == Decimal("1")


def test_close_without_a_time_is_rejected() -> None:
    position = _position()

    with pytest.raises(ValueError):
        close_position(position, Decimal("1"), Decimal("2039.00"), None)  # type: ignore[arg-type]


def test_reduce_then_close_realizes_pnl_only_once() -> None:
    """Reducing does not realize PnL; only a close or a full reduction does."""
    position = _position(quantity=Decimal("2"), entry_price=Decimal("2038.00"))
    reduced = reduce_position(
        position, Decimal("1"), Decimal("2039.00"), AT.replace(minute=31)
    )
    closed = close_position(
        reduced, Decimal("1"), Decimal("2040.00"), AT.replace(minute=32)
    )

    # (2040 - 2038) * 1 realized at close, plus 1.00 from the first reduction.
    assert closed.realized_pnl == Decimal("3")


def test_position_equality_is_content_based() -> None:
    assert _position() == _position()
    assert _position() != _position(position_id="pos-2")


def test_position_is_hashable() -> None:
    assert hash(_position()) == hash(_position())


def test_transitions_never_mutate_the_original() -> None:
    position = _position(quantity=Decimal("2"), entry_price=Decimal("2038.00"))
    snapshot = dataclasses_asdict(position)

    reduce_position(position, Decimal("1"), Decimal("2039.00"), AT.replace(minute=31))
    close_position(position, Decimal("1"), Decimal("2040.00"), AT.replace(minute=32))

    assert dataclasses_asdict(position) == snapshot


def dataclasses_asdict(position: Position) -> dict:
    import dataclasses

    return dataclasses.asdict(position)
