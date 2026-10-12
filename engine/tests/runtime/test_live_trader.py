"""Tests for LiveTrader orchestrator and VAR_117 risk integration.

Verifies:
1. LiveTrader initializes with AlpacaFeed, VAR117Strategy, AlpacaBroker, RiskConfig, and KillSwitch.
2. Rebalance execution is permitted while KillSwitch is ARMED.
3. Rebalance execution is strictly BLOCKED when KillSwitch is TRIPPED.
4. Kill-Switch state persists to hash-chained SQLite journal and survives process restarts.
5. WebSocket broadcast queues distribute updates and drop stale clients.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from engine.execution.alpaca_broker import AlpacaBroker
from engine.feed.alpaca_feed import AlpacaFeed
from engine.risk.config import RiskConfig
from engine.risk.killswitch import KillSwitchState
from engine.runtime.live_trader import LiveTrader


@pytest.fixture
def risk_cfg() -> RiskConfig:
    return RiskConfig(
        risk_per_trade=Decimal("0.01"),  # 1% = $1,000 on $100k equity
        daily_loss_limit_r=Decimal("3"),  # 3R = $3,000 daily loss limit
    )


@pytest.fixture
def mock_broker() -> AlpacaBroker:
    return AlpacaBroker(paper_mode=True, initial_mock_cash=Decimal("100000.00"))


@pytest.fixture
def feed() -> AlpacaFeed:
    return AlpacaFeed()


def test_trader_initialization_and_snapshot(
    feed: AlpacaFeed,
    mock_broker: AlpacaBroker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        feed=feed,
        broker=mock_broker,
        risk_config=risk_cfg,
        enable_journal=False,
    )

    snapshot = trader.get_snapshot()
    assert snapshot["strategy"] == "VAR_117 Multi-Asset ETF Momentum Rotation"
    assert snapshot["account_equity"] == "100000.00"
    assert snapshot["cash_balance"] == "100000.00"
    assert snapshot["killswitch"]["state"] == "ARMED"
    assert snapshot["killswitch"]["is_tripped"] is False
    assert snapshot["killswitch"]["limit_r"] == "3"


def test_websocket_subscribe_and_broadcast(
    feed: AlpacaFeed,
    mock_broker: AlpacaBroker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        feed=feed,
        broker=mock_broker,
        risk_config=risk_cfg,
        enable_journal=False,
    )

    q = trader.subscribe()
    trader.broadcast("portfolio_update", {"equity": "100000.00", "cash": "50000.00"})

    msg = q.get_nowait()
    assert msg["type"] == "portfolio_update"
    assert msg["data"]["equity"] == "100000.00"

    trader.unsubscribe(q)
    trader.broadcast("portfolio_update", {"equity": "101000.00"})
    assert q.empty()


@pytest.mark.asyncio
async def test_rebalance_execution_allowed_when_killswitch_armed(
    feed: AlpacaFeed,
    mock_broker: AlpacaBroker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        feed=feed,
        broker=mock_broker,
        risk_config=risk_cfg,
        enable_journal=False,
    )

    orders = await trader.execute_rebalance()
    assert len(orders) > 0
    for o in orders:
        assert o["status"] == "filled"

    snapshot = trader.get_snapshot()
    assert len(snapshot["holdings"]) > 0
    assert len(snapshot["recent_orders"]) == len(orders)


@pytest.mark.asyncio
async def test_rebalance_blocked_when_killswitch_tripped(
    feed: AlpacaFeed,
    mock_broker: AlpacaBroker,
    risk_cfg: RiskConfig,
) -> None:
    now = dt.datetime(2026, 3, 11, 12, 0, tzinfo=dt.UTC)
    trader = LiveTrader(
        feed=feed,
        broker=mock_broker,
        risk_config=risk_cfg,
        enable_journal=False,
        current_time=now,
    )

    # Trip the killswitch with a 3.5R loss (-$3,500 on $100k equity)
    risk_amt = risk_cfg.risk_amount(Decimal("100000.00"))
    trader.killswitch.record_trade(Decimal("-3500.00"), risk_amount=risk_amt, at=now)

    assert trader.killswitch.is_tripped is True
    assert trader.killswitch.state is KillSwitchState.TRIPPED

    # Attempt rebalance: must be refused and return 0 orders
    orders = await trader.execute_rebalance()
    assert orders == []


@pytest.mark.asyncio
async def test_killswitch_restores_from_journal_across_restart(
    tmp_path: Path,
    feed: AlpacaFeed,
    mock_broker: AlpacaBroker,
    risk_cfg: RiskConfig,
) -> None:
    db_path = tmp_path / "journal.db"
    now = dt.datetime(2026, 3, 11, 14, 0, tzinfo=dt.UTC)

    # 1. First process run: record trade that trips the latch
    trader1 = LiveTrader(
        feed=feed,
        broker=mock_broker,
        risk_config=risk_cfg,
        journal_path=db_path,
        enable_journal=True,
        current_time=now,
    )

    risk_amt = risk_cfg.risk_amount(Decimal("100000.00"))
    trader1.killswitch.record_trade(Decimal("-3500.00"), risk_amount=risk_amt, at=now)
    assert trader1.killswitch.is_tripped is True

    # Flush journal
    trader1.journal_writer.stop()  # type: ignore[union-attr]
    trader1.journal_writer.drain(timeout=dt.timedelta(seconds=2))  # type: ignore[union-attr]

    # 2. Simulated restart: instantiate fresh LiveTrader pointing to same journal.db
    trader2 = LiveTrader(
        feed=feed,
        broker=mock_broker,
        risk_config=risk_cfg,
        journal_path=db_path,
        enable_journal=True,
        current_time=now + dt.timedelta(minutes=10),
    )

    assert trader2.killswitch.is_tripped is True
    assert trader2.killswitch.state is KillSwitchState.TRIPPED

    trader2.journal_writer.stop()  # type: ignore[union-attr]
    trader2.journal_writer.drain(timeout=dt.timedelta(seconds=2))  # type: ignore[union-attr]
