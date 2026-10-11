"""Tests for LiveTrader orchestrator and risk integration.

Verifies:
1. LiveTrader initializes with RiskConfig and KillSwitch.
2. Order execution is permitted while KillSwitch is ARMED.
3. Order execution is strictly BLOCKED when KillSwitch is TRIPPED.
4. Realized losses feed into DailyPnLTracker and trip the KillSwitch latch at the -3R ceiling.
5. Kill-Switch state persists to hash-chained SQLite journal and survives process restarts.
6. WebSocket broadcast queues distribute ticks and drop stale clients.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engine.execution.mt5_broker import MT5Broker, MT5OrderResult, OrderSide
from engine.feed.mt5_client import MT5Feed
from engine.market.models import OHLC, Bar, Timeframe
from engine.risk.config import RiskConfig
from engine.risk.killswitch import KillSwitchState
from engine.runtime.live_trader import LiveTrader


@pytest.fixture
def mock_feed() -> MT5Feed:
    feed = MagicMock(spec=MT5Feed)
    feed.symbol = "XAUUSDm"
    feed.connect.return_value = None
    feed.get_account_summary.return_value = {
        "balance": "10000.00",
        "equity": "10000.00",
        "margin": "0.00",
        "free_margin": "10000.00",
        "profit": "0.00",
        "leverage": 100,
        "currency": "USD",
    }
    feed.fetch_historical_bars.return_value = []
    return feed


@pytest.fixture
def mock_broker() -> MT5Broker:
    broker = MagicMock(spec=MT5Broker)
    broker.symbol = "XAUUSDm"
    broker.place_market_order.return_value = MT5OrderResult(
        retcode=10009,
        deal_id=123,
        order_id=456,
        volume=Decimal("0.01"),
        price=Decimal("2700.000"),
        comment="Test",
        success=True,
    )
    broker.get_open_positions.return_value = []
    return broker


@pytest.fixture
def risk_cfg() -> RiskConfig:
    return RiskConfig(
        risk_per_trade=Decimal("0.01"),  # 1% = $100 on $10k equity
        daily_loss_limit_r=Decimal("3"),  # 3R = $300 daily loss limit
    )


def test_trader_initialization_and_snapshot(
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        symbol="XAUUSDm",
        paper_mode=True,
        risk_config=risk_cfg,
        enable_journal=False,
        feed=mock_feed,
        broker=mock_broker,
    )

    snapshot = trader.get_snapshot()
    assert snapshot["symbol"] == "XAUUSDm"
    assert snapshot["account_balance"] == "10000.00"
    assert snapshot["killswitch"]["state"] == "ARMED"
    assert snapshot["killswitch"]["is_tripped"] is False
    assert snapshot["killswitch"]["limit_r"] == "3"


def test_websocket_subscribe_and_broadcast(
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        symbol="XAUUSDm",
        risk_config=risk_cfg,
        enable_journal=False,
        feed=mock_feed,
        broker=mock_broker,
    )

    q = trader.subscribe()
    trader.broadcast("tick", {"bid": "2700.00", "ask": "2700.50"})

    msg = q.get_nowait()
    assert msg["type"] == "tick"
    assert msg["data"]["bid"] == "2700.00"

    trader.unsubscribe(q)
    trader.broadcast("tick", {"bid": "2701.00"})
    assert q.empty()


def test_order_placement_allowed_when_killswitch_is_armed(
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        symbol="XAUUSDm",
        lot_size=Decimal("0.01"),
        risk_config=risk_cfg,
        enable_journal=False,
        feed=mock_feed,
        broker=mock_broker,
    )

    assert trader.killswitch.is_tripped is False

    # Simulate setup directly on strategy
    setup = MagicMock()
    setup.side.value = "BUY"
    setup.entry_price = Decimal("2700.00")
    setup.stop_loss = Decimal("2690.00")
    setup.take_profit = Decimal("2725.00")
    setup.bar_time = dt.datetime(2026, 3, 11, 8, 30, tzinfo=dt.UTC)
    setup.reason = "Judas Long"

    trader.judas_strategy.on_bar = MagicMock(return_value=setup)  # type: ignore[method-assign]

    bar = Bar(
        timeframe=Timeframe.M5.value,
        timestamp_utc=dt.datetime(2026, 3, 11, 8, 30, tzinfo=dt.UTC),
        complete=True,
        volume=100,
        bid_ohlc=OHLC(Decimal("2700"), Decimal("2705"), Decimal("2695"), Decimal("2700")),
        ask_ohlc=OHLC(Decimal("2700"), Decimal("2705"), Decimal("2695"), Decimal("2700")),
    )

    trader._on_m5_bar_closed(bar)

    # Broker should have received order
    mock_broker.place_market_order.assert_called_once_with(
        side=OrderSide.BUY,
        quantity=Decimal("0.01"),
        stop_loss=Decimal("2690.00"),
        take_profit=Decimal("2725.00"),
        comment="ICT Judas",
    )
    assert len(trader._completed_trades) == 1


def test_order_placement_blocked_when_killswitch_is_tripped(
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        symbol="XAUUSDm",
        risk_config=risk_cfg,
        enable_journal=False,
        feed=mock_feed,
        broker=mock_broker,
    )

    # Trip the switch by recording 3R of loss (-$300)
    now = dt.datetime(2026, 3, 11, 12, 0, tzinfo=dt.UTC)
    trader.record_trade_result(Decimal("-300.00"), close_time=now)
    assert trader.killswitch.is_tripped is True
    assert trader.killswitch.state is KillSwitchState.TRIPPED

    # Now attempt a signal
    setup = MagicMock()
    setup.side.value = "BUY"
    setup.entry_price = Decimal("2700.00")
    setup.stop_loss = Decimal("2690.00")
    setup.take_profit = Decimal("2725.00")
    setup.bar_time = now
    setup.reason = "Judas Long"
    trader.judas_strategy.on_bar = MagicMock(return_value=setup)  # type: ignore[method-assign]

    bar = Bar(
        timeframe=Timeframe.M5.value,
        timestamp_utc=now,
        complete=True,
        volume=100,
        bid_ohlc=OHLC(Decimal("2700"), Decimal("2705"), Decimal("2695"), Decimal("2700")),
        ask_ohlc=OHLC(Decimal("2700"), Decimal("2705"), Decimal("2695"), Decimal("2700")),
    )

    trader._on_m5_bar_closed(bar)

    # Broker must NOT have been called
    mock_broker.place_market_order.assert_not_called()


def test_killswitch_restores_tripped_state_from_journal(
    tmp_path: Path,
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    db_path = tmp_path / "journal.db"
    now = dt.datetime(2026, 3, 11, 14, 0, tzinfo=dt.UTC)

    # 1. First process run: record trades that trip the latch
    trader1 = LiveTrader(
        symbol="XAUUSDm",
        risk_config=risk_cfg,
        journal_path=db_path,
        enable_journal=True,
        feed=mock_feed,
        broker=mock_broker,
        current_time=now,
    )

    # Record 3R loss (-$350 on $10k equity = -3.5R <= -3R)
    trader1.record_trade_result(Decimal("-350.00"), close_time=now)
    assert trader1.killswitch.is_tripped is True

    # Flush journal
    trader1.journal_writer.stop()  # type: ignore[union-attr]
    trader1.journal_writer.drain(timeout=dt.timedelta(seconds=2))  # type: ignore[union-attr]

    # 2. Simulated restart: instantiate fresh LiveTrader pointing to same journal.db
    trader2 = LiveTrader(
        symbol="XAUUSDm",
        risk_config=risk_cfg,
        journal_path=db_path,
        enable_journal=True,
        feed=mock_feed,
        broker=mock_broker,
        current_time=now + dt.timedelta(minutes=10),
    )

    # The new instance must come up TRIPPED from the journal history!
    assert trader2.killswitch.is_tripped is True
    assert trader2.killswitch.state is KillSwitchState.TRIPPED
    assert trader2.get_snapshot()["killswitch"]["is_tripped"] is True

    if trader2.journal_writer is not None:
        trader2.journal_writer.stop()
        trader2.journal_writer.drain(timeout=dt.timedelta(seconds=2))


def test_position_closure_detection(
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        symbol="XAUUSDm",
        risk_config=risk_cfg,
        enable_journal=False,
        feed=mock_feed,
        broker=mock_broker,
    )

    # Initial open position
    pos = {
        "ticket": 999,
        "time": "2026-03-11T08:00:00Z",
        "side": "BUY",
        "volume": "0.01",
        "open_price": "2700.00",
        "current_price": "2710.00",
        "sl": "2690.00",
        "tp": "2725.00",
        "profit": "100.00",
        "comment": "Trade",
    }
    trader._open_positions = [pos]

    # Position now closed (empty list from broker)
    mock_broker.get_open_positions.return_value = []
    trader._refresh_positions()

    assert trader._realized_pnl_today == Decimal("100.00")
    assert trader.killswitch.is_tripped is False


def test_dynamic_lot_sizing(
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        symbol="XAUUSDm",
        lot_size=None,
        risk_config=risk_cfg,
        enable_journal=False,
        feed=mock_feed,
        broker=mock_broker,
    )

    # $10,000 equity, 1% risk = $100 budget.
    # Entry = 2700.00, SL = 2695.00 -> stop_dist = $5.00 -> $500 risk/lot.
    # $100 / $500 = 0.20 lots.
    lot = trader.calculate_lot_size(
        entry=Decimal("2700.00"),
        stop_loss=Decimal("2695.00"),
        equity=Decimal("10000.00"),
    )
    assert lot == Decimal("0.20")

    # Stop distance too wide for account: $10 risk on $1,000 equity with $25 stop
    # $10 / ($25 * 100) = 0.004 lots < 0.01 min lot -> refused (0).
    too_wide_lot = trader.calculate_lot_size(
        entry=Decimal("2700.00"),
        stop_loss=Decimal("2675.00"),
        equity=Decimal("1000.00"),
    )
    assert too_wide_lot == Decimal("0")


def test_closed_deal_lookup_updates_completed_trades(
    mock_feed: MT5Feed,
    mock_broker: MT5Broker,
    risk_cfg: RiskConfig,
) -> None:
    trader = LiveTrader(
        symbol="XAUUSDm",
        risk_config=risk_cfg,
        enable_journal=False,
        feed=mock_feed,
        broker=mock_broker,
    )

    trader._completed_trades = [
        {
            "id": 1234,
            "ticket": 1234,
            "side": "BUY",
            "volume": "0.01",
            "entry": "2700.000",
            "sl": "2695.000",
            "tp": "2715.000",
            "time": "2026-03-11T08:00:00+00:00",
            "status": "OPEN",
            "strategy": "ICT Judas",
        }
    ]
    trader._open_positions = [
        {
            "ticket": 1234,
            "time": "2026-03-11T08:00:00+00:00",
            "side": "BUY",
            "volume": "0.01",
            "open_price": "2700.000",
            "current_price": "2710.000",
            "sl": "2695.000",
            "tp": "2715.000",
            "profit": "100.00",
            "comment": "Trade",
        }
    ]

    # Position closes on MT5 with exact deal history
    mock_broker.get_open_positions.return_value = []
    mock_broker.get_closed_deal_for_position.return_value = {
        "ticket": 5678,
        "order": 1234,
        "position_id": 1234,
        "price": Decimal("2714.500"),
        "profit": Decimal("145.00"),
        "time": dt.datetime(2026, 3, 11, 8, 30, tzinfo=dt.UTC),
        "comment": "[tp 2715.000]",
    }

    trader._refresh_positions()

    assert trader._realized_pnl_today == Decimal("145.00")
    assert len(trader._completed_trades) == 1
    trade = trader._completed_trades[0]
    assert trade["status"] == "CLOSED"
    assert trade["exit"] == "2714.500"
    assert trade["profit"] == "+145.00"

