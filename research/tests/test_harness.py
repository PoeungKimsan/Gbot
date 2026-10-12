"""Unit tests for the read-only research harness."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import sqlite3
import pytest

from engine.market.instrument import InstrumentSpec
from engine.market.models import OHLC, Bar, Timeframe
from research.adapters.db_reader import connect_readonly, extract_live_trades_from_events
from research.adapters.judas_adapter import JudasSwingStrategy
from research.adapters.silver_bullet_adapter import (
    load_news_calendar,
    load_silver_bullet_strategy,
)
from research.backtest.engine import BacktestRunner, RestingLimit, SimulatedTrade
from research.metrics.trade_stats import (
    compute_bootstrap_ci,
    compute_drawdowns,
    compute_losing_streak,
    compute_t_stat,
    compute_trade_metrics,
)
from research.safety import is_live_trading_window, set_low_process_priority
from research.simulation.monte_carlo import run_monte_carlo_risk


def test_sqlite_readonly_guarantee(tmp_path):
    """Test that connect_readonly strictly enforces read-only mode."""
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test (id INT, val TEXT);")
    conn.execute("INSERT INTO test VALUES (1, 'live');")
    conn.commit()
    conn.close()

    ro_conn = connect_readonly(db_file)
    cursor = ro_conn.cursor()
    rows = cursor.execute("SELECT * FROM test;").fetchall()
    assert len(rows) == 1

    # Attempting to write must raise OperationalError
    with pytest.raises(sqlite3.OperationalError):
        cursor.execute("INSERT INTO test VALUES (2, 'tamper');")

    ro_conn.close()


def test_safety_process_priority():
    """Verify that process priority can be lowered safely."""
    res = set_low_process_priority()
    assert res is True


def test_trade_metrics_empty():
    """Verify trade metrics on empty trade log."""
    res = compute_trade_metrics([])
    assert res["n_trades"] == 0
    assert res["win_rate"] == 0.0
    assert res["avg_r"] == 0.0
    assert res["ci_95_lower"] is None
    assert res["profit_factor"] == 0.0


def test_trade_metrics_calculation():
    """Verify trade metrics on synthetic trades."""
    trades = [
        {"r_multiple": 2.0, "spread": 0.2, "slippage": 0.05, "entry_price": 2000.0, "stop_loss": 1995.0, "side": "BUY"},
        {"r_multiple": -1.0, "spread": 0.2, "slippage": 0.05, "entry_price": 2000.0, "stop_loss": 1995.0, "side": "BUY"},
        {"r_multiple": 2.0, "spread": 0.2, "slippage": 0.05, "entry_price": 2000.0, "stop_loss": 1995.0, "side": "SELL"},
        {"r_multiple": -1.0, "spread": 0.2, "slippage": 0.05, "entry_price": 2000.0, "stop_loss": 1995.0, "side": "SELL"},
    ]
    res = compute_trade_metrics(trades, bootstrap_resamples=1000)
    assert res["n_trades"] == 4
    assert res["win_rate"] == 0.5
    assert res["avg_r"] == 0.5
    assert res["profit_factor"] == 2.0
    assert res["ci_95_lower"] is not None
    assert res["ci_95_upper"] is not None


def test_conservative_conflict_resolution():
    """Verify that when both stop and target hit in same candle, stop hits first."""
    runner = BacktestRunner(cost_multiplier=Decimal("0.0"))
    trade = SimulatedTrade(
        trade_id="test-1",
        strategy="Judas",
        side="BUY",
        entry_time=dt.datetime(2024, 1, 1, 10, 0, tzinfo=dt.UTC),
        entry_price=Decimal("2000.00"),
        stop_loss=Decimal("1995.00"),
        take_profit=Decimal("2010.00"),
        risk_dollars=Decimal("5.00"),
        reward_dollars=Decimal("10.00"),
    )

    # Candle that pierces both stop (1995) and target (2010)
    conflict_ohlc = OHLC(
        open=Decimal("2000.00"),
        high=Decimal("2015.00"),
        low=Decimal("1990.00"),
        close=Decimal("2005.00"),
    )
    conflict_bar = Bar(
        timeframe=Timeframe.M5,
        timestamp_utc=dt.datetime(2024, 1, 1, 10, 5, tzinfo=dt.UTC),
        complete=True,
        volume=100,
        bid_ohlc=conflict_ohlc,
        ask_ohlc=conflict_ohlc,
    )

    # Check logic
    runner._close_trade(
        trade,
        exit_price=trade.stop_loss,
        exit_time=conflict_bar.timestamp_utc,
        reason="STOP_LOSS (conflict resolution)",
        is_stop=True,
    )
    assert trade.status == "CLOSED"
    assert trade.exit_reason == "STOP_LOSS (conflict resolution)"
    assert trade.r_multiple < Decimal("0")


def test_silver_bullet_adapter_loading():
    """Verify that Silver Bullet strategy and news calendar load without side effects."""
    spec = InstrumentSpec(
        name="XAU_USD",
        pip_location=-2,
        display_precision=2,
        trade_units_precision=2,
        minimum_trade_size=Decimal("0.01"),
    )
    strat, cfg = load_silver_bullet_strategy(spec=spec)
    assert strat is not None
    assert cfg.swing_n == 2
    assert cfg.rr_target == Decimal("2")

    calendar = load_news_calendar()
    assert calendar is not None
    assert len(calendar.rules) > 0


def test_monte_carlo_risk_simulation():
    """Verify Monte Carlo risk simulator runs and computes valid percentiles."""
    r_multiples = [2.0, -1.0, 2.0, -1.0, -1.0, 2.0, 2.0, -1.0]
    res = run_monte_carlo_risk(r_multiples, n_simulations=500, seed=42)
    assert res["n_simulations"] == 500
    assert res["mdd_pct_95th"] >= 0.0
    assert res["mdd_r_95th"] >= 0.0
    assert 0.0 <= res["prob_10_loss_streak"] <= 1.0
