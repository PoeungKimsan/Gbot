"""Idempotency tests with broker stub simulating duplicate rejection and partial fills."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from engine.execution.alpaca_broker import AlpacaBroker


class IdempotentBrokerStub(AlpacaBroker):
    """Stub broker enforcing duplicate client_order_id rejection and tracking calls."""

    def __init__(self, partial_sell_ratio: Decimal = Decimal("1.0")) -> None:
        super().__init__(mock=True)
        self.submitted_cids: set[str] = set()
        self.call_order: list[str] = []
        self.partial_sell_ratio = partial_sell_ratio

    async def _place_order(
        self,
        symbol: str,
        qty: Decimal,
        side: str,
        price: Decimal,
        notional: Decimal | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        if client_order_id:
            if client_order_id in self.submitted_cids:
                raise RuntimeError(f"Duplicate client_order_id rejected: {client_order_id}")
            self.submitted_cids.add(client_order_id)

        self.call_order.append(f"{side}:{symbol}")
        res = await super()._place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            price=price,
            notional=notional,
            client_order_id=client_order_id,
        )
        if side == "sell" and self.partial_sell_ratio < Decimal("1.0"):
            trade_val = notional if notional is not None else (qty * price)
            fill_notional = trade_val * self.partial_sell_ratio
            fill_qty = qty * self.partial_sell_ratio
            res["notional"] = str(fill_notional)
            res["qty"] = str(fill_qty)
        return res


@pytest.mark.asyncio
async def test_idempotency_crash_restart_same_signal_date() -> None:
    broker = IdempotentBrokerStub()
    target_weights = {"SPY": Decimal("0.50"), "QQQ": Decimal("0.50")}
    prices = {"SPY": Decimal("500.00"), "QQQ": Decimal("400.00")}
    reb_date = dt.date(2026, 10, 30)

    # First run submits orders
    orders1 = await broker.rebalance_portfolio(target_weights, prices, rebalance_date=reb_date)
    assert len(orders1) == 2
    # Restart on same signal_date detects existing and submits 0
    orders2 = await broker.rebalance_portfolio(target_weights, prices, rebalance_date=reb_date)
    assert len(orders2) == 0


@pytest.mark.asyncio
async def test_idempotency_open_orders_not_resubmitted() -> None:
    broker = IdempotentBrokerStub()
    # Open order already present at start
    broker._mock_orders.append({"symbol": "GLD", "status": "open", "client_order_id": "other-gld"})
    orders = await broker.rebalance_portfolio(
        {"GLD": Decimal("0.30")}, {"GLD": Decimal("200.00")}, rebalance_date=dt.date(2026, 11, 2)
    )
    assert not any(o["symbol"] == "GLD" for o in orders)


@pytest.mark.asyncio
async def test_idempotency_partial_fill_caps_buys_with_cash_buffer() -> None:
    stub = IdempotentBrokerStub(partial_sell_ratio=Decimal("0.50"))
    stub._mock_positions["IEF"] = {
        "symbol": "IEF",
        "qty": Decimal("100"),
        "current_price": Decimal("100.00"),
        "market_value": Decimal("10000.00"),
        "unrealized_pnl": Decimal("0.00"),
        "avg_price": Decimal("100.00"),
    }
    stub._mock_cash = Decimal("0.00")

    reb_orders = await stub.rebalance_portfolio(
        {"SPY": Decimal("0.80"), "IEF": Decimal("0.00")},
        {"SPY": Decimal("500.00"), "IEF": Decimal("100.00")},
        rebalance_date=dt.date(2026, 11, 30),
        cash_buffer=Decimal("500.00"),
    )
    # Sells occur before buys
    assert stub.call_order[0].startswith("sell:IEF")
    assert stub.call_order[1].startswith("buy:SPY")
    # 50% partial fill ($5,000) minus $500 cash buffer caps buys at $4,500
    buy_order = next(o for o in reb_orders if o["side"] == "buy")
    assert Decimal(str(buy_order["notional"])) == Decimal("4500.00")


@pytest.mark.asyncio
async def test_broker_delayed_fill_mock_confirmation() -> None:
    broker = AlpacaBroker(mock=True, fill_delay_polls=2)
    broker._mock_cash = Decimal("10000.00")
    orders = await broker.rebalance_portfolio(
        {"SPY": Decimal("1.00")},
        {"SPY": Decimal("500.00")},
        rebalance_date=dt.date(2026, 10, 30),
    )
    assert len(orders) == 1
    assert orders[0]["status"] == "filled"

