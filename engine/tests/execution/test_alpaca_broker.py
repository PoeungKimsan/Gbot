"""Unit tests for AlpacaBroker execution and delta rebalancing."""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.execution.alpaca_broker import AlpacaBroker, AlpacaBrokerError


@pytest.mark.asyncio
async def test_alpaca_mock_broker_initial_state() -> None:
    broker = AlpacaBroker(paper_mode=True)
    summary = await broker.get_account_summary()

    assert summary["equity"] == Decimal("100000.00")
    assert summary["cash"] == Decimal("100000.00")
    assert summary["portfolio_value"] == Decimal("0.00")

    positions = await broker.get_open_positions()
    assert len(positions) == 0


@pytest.mark.asyncio
async def test_alpaca_mock_rebalance_buys() -> None:
    broker = AlpacaBroker(paper_mode=True, initial_mock_cash=Decimal("100000.00"))

    # Target: 50% SPY at $500, 50% QQQ at $400
    target_weights = {
        "SPY": Decimal("0.5000"),
        "QQQ": Decimal("0.5000"),
    }
    prices = {
        "SPY": Decimal("500.00"),
        "QQQ": Decimal("400.00"),
    }

    orders = await broker.submit_rebalance_orders(target_weights, prices)
    assert len(orders) == 2

    # Verify orders are market buys
    for o in orders:
        assert o["side"] == "buy"
        assert o["status"] == "filled"

    # Verify positions after fill
    positions = await broker.get_open_positions()
    assert len(positions) == 2

    pos_dict = {p["symbol"]: p for p in positions}
    assert pos_dict["SPY"]["qty"] == Decimal("100.0000")  # $50,000 / $500 = 100
    assert pos_dict["QQQ"]["qty"] == Decimal("125.0000")  # $50,000 / $400 = 125

    summary = await broker.get_account_summary()
    assert summary["equity"] == Decimal("100000.00")
    assert summary["cash"] == Decimal("0.00")


@pytest.mark.asyncio
async def test_alpaca_mock_rebalance_sells_before_buys() -> None:
    broker = AlpacaBroker(paper_mode=True, initial_mock_cash=Decimal("100000.00"))

    # Initial allocation: 100% SPY
    await broker.submit_rebalance_orders(
        {"SPY": Decimal("1.0000")},
        {"SPY": Decimal("500.00"), "GLD": Decimal("200.00")},
    )

    # Next rebalance: Rotate to 50% SPY, 50% GLD
    new_weights = {
        "SPY": Decimal("0.5000"),
        "GLD": Decimal("0.5000"),
    }
    prices = {
        "SPY": Decimal("500.00"),
        "GLD": Decimal("200.00"),
    }

    orders = await broker.submit_rebalance_orders(new_weights, prices)
    assert len(orders) == 2

    # First order must be SELL (reducing SPY), second must be BUY (adding GLD)
    assert orders[0]["symbol"] == "SPY"
    assert orders[0]["side"] == "sell"
    assert orders[1]["symbol"] == "GLD"
    assert orders[1]["side"] == "buy"


@pytest.mark.asyncio
async def test_alpaca_mock_liquidate_all() -> None:
    broker = AlpacaBroker(paper_mode=True, initial_mock_cash=Decimal("100000.00"))
    await broker.submit_rebalance_orders(
        {"SPY": Decimal("1.0000")},
        {"SPY": Decimal("500.00")},
    )

    positions_before = await broker.get_open_positions()
    assert len(positions_before) == 1

    closed = await broker.liquidate_all_positions()
    assert len(closed) == 1
    assert closed[0]["symbol"] == "SPY"

    positions_after = await broker.get_open_positions()
    assert len(positions_after) == 0

    summary = await broker.get_account_summary()
    assert summary["cash"] == Decimal("100000.00")


@pytest.mark.asyncio
async def test_alpaca_mock_dust_liquidation() -> None:
    broker = AlpacaBroker(paper_mode=True, initial_mock_cash=Decimal("100.00"))
    # Manually inject a dust position worth $3.50 (less than the $5 rebalance threshold)
    broker._mock_positions["TLT"] = {
        "qty": Decimal("0.0350"),
        "avg_price": Decimal("100.00"),
    }
    broker._mock_cash = Decimal("96.50")

    # Rotate with target weight 0 for TLT (100% into cash)
    target_weights = {"TLT": Decimal("0.0000")}
    prices = {"TLT": Decimal("100.00")}

    orders = await broker.submit_rebalance_orders(target_weights, prices)
    assert len(orders) == 1
    assert orders[0]["symbol"] == "TLT"
    assert orders[0]["side"] == "sell"
    assert orders[0]["qty"] == "0.0350"

    positions = await broker.get_open_positions()
    assert len(positions) == 0


def test_mode_url_mismatch() -> None:
    # 1. paper_mode is True, but base_url points to live endpoint
    with pytest.raises(AlpacaBrokerError, match=r"Mode/URL mismatch"):
        AlpacaBroker(
            mode="paper",
            paper_mode=True,
            base_url="https://api.alpaca.markets",
            api_key="fake_key",
            api_secret="fake_secret",
        )

    # 2. mode is live, but base_url points to paper endpoint
    with pytest.raises(AlpacaBrokerError, match=r"Mode/URL mismatch"):
        AlpacaBroker(
            mode="live",
            paper_mode=False,
            base_url="https://paper-api.alpaca.markets",
            api_key="fake_key",
            api_secret="fake_secret",
        )


@pytest.mark.asyncio
async def test_get_portfolio_history() -> None:
    broker = AlpacaBroker(paper_mode=True, initial_mock_cash=Decimal("100000.00"))
    history = await broker.get_portfolio_history(period="2D", timeframe="5Min")
    assert len(history) > 0
    for pt in history:
        assert isinstance(pt["timestamp"], int)
        assert isinstance(pt["equity"], Decimal)
        assert isinstance(pt["profit_loss"], Decimal)
        assert isinstance(pt["profit_loss_pct"], Decimal)


