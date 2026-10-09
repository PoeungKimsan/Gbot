"""Unit tests for :mod:`engine.domain.orders`.

The invariant under test is that an order never mutates. Every state change returns a new
object, so a partially-applied order cannot exist and every transition is individually
auditable in the journal.
"""

import datetime as dt
from decimal import Decimal

import pytest

from engine.domain.orders import (
    Order,
    OrderKind,
    OrderRequest,
    OrderSide,
    OrderStateError,
    OrderStatus,
    transition,
)

UTC = dt.UTC


def _request(**overrides: object) -> OrderRequest:
    defaults: dict[str, object] = {
        "instrument": "XAU_USD",
        "side": OrderSide.BUY,
        "kind": OrderKind.MARKET,
        "quantity": Decimal("1"),
        "submitted_at": dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
    }
    defaults.update(overrides)
    return OrderRequest(**defaults)  # type: ignore[arg-type]


def _order(**overrides: object) -> Order:
    defaults: dict[str, object] = {
        "order_id": "ord-1",
        "run_id": "run-1",
        "request": _request(),
    }
    defaults.update(overrides)
    return Order(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# enums
# --------------------------------------------------------------------------- #
def test_order_side_wire_values() -> None:
    assert OrderSide.BUY.value == "BUY"
    assert OrderSide.SELL.value == "SELL"


def test_order_kind_wire_values() -> None:
    assert {k.value for k in OrderKind} == {"MARKET", "LIMIT", "STOP"}


def test_order_status_covers_the_lifecycle() -> None:
    expected = {
        "PENDING",
        "ACKNOWLEDGED",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELLED",
        "REJECTED",
        "EXPIRED",
    }
    assert {s.value for s in OrderStatus} == expected


def test_order_side_from_value() -> None:
    assert OrderSide.from_value("buy") is OrderSide.BUY
    assert OrderSide.from_value("sideways") is None


def test_order_kind_from_value() -> None:
    assert OrderKind.from_value("limit") is OrderKind.LIMIT
    assert OrderKind.from_value("stop_limit") is None


def test_order_status_from_value() -> None:
    assert OrderStatus.from_value("filled") is OrderStatus.FILLED
    assert OrderStatus.from_value("unknown") is None


def test_order_side_opposite() -> None:
    assert OrderSide.BUY.opposite is OrderSide.SELL
    assert OrderSide.SELL.opposite is OrderSide.BUY


# --------------------------------------------------------------------------- #
# OrderRequest
# --------------------------------------------------------------------------- #
def test_request_requires_a_limit_price_for_limit_orders() -> None:
    with pytest.raises(ValueError, match="limit_price"):
        _request(kind=OrderKind.LIMIT, limit_price=None)


def test_request_rejects_a_limit_price_on_a_market_order() -> None:
    with pytest.raises(ValueError, match="limit_price"):
        _request(kind=OrderKind.MARKET, limit_price=Decimal("2038.00"))


def test_request_requires_a_stop_price_for_stop_orders() -> None:
    with pytest.raises(ValueError, match="stop_price"):
        _request(kind=OrderKind.STOP, stop_price=None)


def test_request_rejects_a_stop_price_on_a_market_order() -> None:
    with pytest.raises(ValueError, match="stop_price"):
        _request(kind=OrderKind.MARKET, stop_price=Decimal("2040.00"))


@pytest.mark.parametrize("bad", [Decimal("0"), Decimal("-1"), Decimal("NaN"), 1.5])
def test_request_rejects_invalid_quantity(bad: object) -> None:
    with pytest.raises(ValueError, match="quantity"):
        _request(quantity=bad)


def test_request_rejects_a_non_positive_price() -> None:
    for bad in (Decimal("0"), Decimal("-0.01")):
        with pytest.raises(ValueError, match="limit_price"):
            _request(kind=OrderKind.LIMIT, limit_price=bad)


def test_request_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _request(submitted_at=dt.datetime(2026, 1, 15, 14, 30))


def test_request_is_frozen() -> None:
    request = _request()

    with pytest.raises(Exception):  # noqa: B017
        request.instrument = "EUR_USD"  # type: ignore[misc]


def test_request_accepts_a_stop_loss_tag() -> None:
    """Stop-loss orders are tagged so the simulator can pair them with entries."""
    request = _request(
        kind=OrderKind.STOP,
        stop_price=Decimal("2030.00"),
        tag="stop_loss",
        parent_order_id="ord-entry",
    )

    assert request.tag == "stop_loss"
    assert request.parent_order_id == "ord-entry"


def test_request_defaults_are_neutral() -> None:
    request = _request()

    assert request.tag == ""
    assert request.parent_order_id is None
    assert request.limit_price is None
    assert request.stop_price is None


# --------------------------------------------------------------------------- #
# Order immutable transitions
# --------------------------------------------------------------------------- #
def test_order_starts_pending_with_no_fill() -> None:
    order = _order()

    assert order.status is OrderStatus.PENDING
    assert order.filled_quantity == Decimal(0)
    assert order.average_fill_price is None


def test_order_is_frozen() -> None:
    order = _order()

    with pytest.raises(Exception):  # noqa: B017
        order.status = OrderStatus.FILLED  # type: ignore[misc]


def test_acknowledge_returns_a_new_order() -> None:
    order = _order()
    acknowledged = transition(
        order,
        OrderStatus.ACKNOWLEDGED,
        at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC),
    )

    assert order is not acknowledged
    assert order.status is OrderStatus.PENDING
    assert acknowledged.status is OrderStatus.ACKNOWLEDGED


def test_fill_records_amount_and_price() -> None:
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    )
    filled = order.record_fill(
        Decimal("0.4"), Decimal("2038.25"), dt.datetime(2026, 1, 15, 14, 31, 5, tzinfo=UTC)
    )

    assert filled.filled_quantity == Decimal("0.4")
    assert filled.average_fill_price == Decimal("2038.25")
    assert filled.status is OrderStatus.PARTIALLY_FILLED


def test_partial_fills_average_out() -> None:
    at = dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )
    order = order.record_fill(Decimal("0.4"), Decimal("2038.20"), at)
    order = order.record_fill(Decimal("0.6"), Decimal("2038.30"), at)

    assert order.filled_quantity == Decimal("1.0")
    # (0.4 * 2038.20 + 0.6 * 2038.30) / 1.0 = 2038.26
    assert order.average_fill_price == Decimal("2038.26")


def test_fill_to_become_complete_when_fully_filled() -> None:
    at = dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )
    order = order.record_fill(Decimal("1"), Decimal("2038.25"), at)

    assert order.status is OrderStatus.FILLED
    assert order.remaining_quantity == Decimal(0)
    assert order.is_complete is True


def test_fill_rejects_overfilling() -> None:
    at = dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )
    order = order.record_fill(Decimal("0.9"), Decimal("2038.25"), at)

    with pytest.raises(OrderStateError, match="exceeds"):
        order.record_fill(Decimal("0.2"), Decimal("2038.26"), at)


def test_fill_rejects_a_non_positive_quantity() -> None:
    at = dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )

    with pytest.raises(ValueError, match="positive"):
        order.record_fill(Decimal("0"), Decimal("2038.25"), at)


def test_fill_rejects_a_filled_order() -> None:
    at = dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )
    order = order.record_fill(Decimal("1"), Decimal("2038.25"), at)

    with pytest.raises(OrderStateError, match="terminal"):
        order.record_fill(Decimal("0.1"), Decimal("2038.26"), at)


def test_cancel_records_the_timestamp() -> None:
    at = dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    ).cancel(at)

    assert order.status is OrderStatus.CANCELLED
    assert order.cancelled_at == at


def test_reject_records_the_reason() -> None:
    at = dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    ).reject("insufficient margin", at)

    assert order.status is OrderStatus.REJECTED
    assert order.rejection_reason == "insufficient margin"
    assert order.rejected_at == at


def test_reject_without_a_reason_is_rejected() -> None:
    acknowledged = transition(
        _order(),
        OrderStatus.ACKNOWLEDGED,
        at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="reason"):
        acknowledged.reject("  ", dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC))


def test_filled_orders_are_terminal() -> None:
    at = dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )
    order = order.record_fill(Decimal("1"), Decimal("2038.25"), at)

    cancelled_at = dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC)
    for target in (OrderStatus.CANCELLED, OrderStatus.ACKNOWLEDGED, OrderStatus.FILLED):
        with pytest.raises(OrderStateError):
            transition(order, target, at=cancelled_at)


@pytest.mark.parametrize("status", [OrderStatus.CANCELLED, OrderStatus.REJECTED])
def test_cancelled_and_rejected_orders_are_terminal(status: OrderStatus) -> None:
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )
    if status is OrderStatus.CANCELLED:
        order = order.cancel(dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC))
    else:
        order = order.reject("no", dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC))

    with pytest.raises(OrderStateError):
        transition(order, OrderStatus.ACKNOWLEDGED)


def test_pending_cannot_cancel_before_acknowledgement() -> None:
    """A pending order has never reached the venue, so there is nothing to cancel."""
    order = _order()

    with pytest.raises(OrderStateError, match="PENDING"):
        order.cancel(dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC))


def test_cannot_acknowledge_twice() -> None:
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )

    with pytest.raises(OrderStateError):
        transition(order, OrderStatus.ACKNOWLEDGED)


def test_remaining_quantity_uses_decimal_arithmetic() -> None:
    at = dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC)
    order = transition(
        _order(), OrderStatus.ACKNOWLEDGED, at=dt.datetime(2026, 1, 15, 14, 30, 1, tzinfo=UTC)
    )
    order = order.record_fill(Decimal("0.35"), Decimal("2038.25"), at)

    assert order.remaining_quantity == Decimal("0.65")
    assert isinstance(order.remaining_quantity, Decimal)


def test_order_equality_is_content_based() -> None:
    assert _order() == _order()
    assert _order() != _order(order_id="ord-2")


def test_order_is_hashable() -> None:
    assert hash(_order()) == hash(_order())


def test_transition_accepts_only_the_acknowledged_entry() -> None:
    with pytest.raises(OrderStateError, match="directly"):
        transition(_order(), OrderStatus.FILLED)
