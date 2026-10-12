"""Live Trading Engine for VAR_117 Multi-Asset ETF Momentum Rotation.

Bridges real-time ETF quotes, VAR_117 strategy evaluation, Alpaca broker
delta rebalancing, Decimal risk controls with latching drawdown kill-switch
and hash-chained SQLite journal persistence, and real-time WebSocket streaming
for the institutional web dashboard.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import sqlite3
from collections.abc import Coroutine
from decimal import Decimal
from pathlib import Path
from typing import Any

from engine.domain.events import DomainEvent, EventType
from engine.execution.alpaca_broker import AlpacaBroker
from engine.feed.alpaca_feed import AlpacaFeed
from engine.journal.writer import JournalWriter
from engine.market.instrument import InstrumentSpec
from engine.risk.config import RiskConfig, load_risk_config
from engine.risk.killswitch import KillSwitch, KillSwitchState
from engine.runtime.telegram import TelegramNotifier
from engine.strategy.etf_momentum import (
    ETF_METADATA,
    PortfolioAllocationSignal,
    VAR117Strategy,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_INSTRUMENT",
    "LiveTrader",
    "global_trader",
]

DEFAULT_INSTRUMENT = InstrumentSpec(
    name="US_ETF_PORTFOLIO",
    pip_location=-2,
    display_precision=2,
    trade_units_precision=4,
    minimum_trade_size=Decimal("0.0001"),
)


def _read_journal_killswitch_events(path: Path) -> list[DomainEvent]:
    """Replay kill-switch transitions from SQLite journal to restore latch across restarts."""
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='journal'"
            )
            if not cursor.fetchone():
                return []
            rows = cursor.execute(
                "SELECT event_id, event_type, occurred_at, payload FROM journal "
                "WHERE event_type = ? ORDER BY sequence",
                (EventType.RISK_KILLSWITCH.value,),
            ).fetchall()
        finally:
            conn.close()

        events: list[DomainEvent] = []
        for row in rows:
            raw_payload = json.loads(row["payload"])
            payload = raw_payload.get("payload", raw_payload)
            events.append(
                DomainEvent(
                    event_id=row["event_id"],
                    event_type=EventType(row["event_type"]),
                    occurred_at=dt.datetime.fromisoformat(row["occurred_at"]),
                    payload=payload,
                )
            )
        return events
    except Exception as exc:
        logger.warning("Could not read journal events from %s: %s", path, exc)
        return []


class LiveTrader:
    """Orchestrates ETF feed, VAR_117 strategy, Alpaca broker, and WebSocket dispatch."""

    def __init__(
        self,
        feed: AlpacaFeed | None = None,
        broker: AlpacaBroker | None = None,
        strategy: VAR117Strategy | None = None,
        risk_config: RiskConfig | None = None,
        risk_config_path: Path | str = "config/risk.yaml",
        journal_path: Path | str = "journal.db",
        enable_journal: bool = True,
        current_time: dt.datetime | None = None,
        telegram: TelegramNotifier | None = None,
    ) -> None:
        self.instrument_spec = DEFAULT_INSTRUMENT
        self._current_time = current_time
        self.telegram = telegram if telegram is not None else TelegramNotifier()

        # Feed, Strategy, Broker
        self.feed = feed if feed is not None else AlpacaFeed()
        self.broker = broker if broker is not None else AlpacaBroker()
        self.strategy = strategy if strategy is not None else VAR117Strategy()

        # Risk & Kill-Switch
        if risk_config is not None:
            self.risk_config = risk_config
        else:
            try:
                self.risk_config = load_risk_config(risk_config_path)
            except Exception as exc:
                logger.warning(
                    "Could not load risk config from %s (%s); using default",
                    risk_config_path,
                    exc,
                )
                self.risk_config = RiskConfig(
                    risk_per_trade=Decimal("0.01"),
                    daily_loss_limit_r=Decimal("3"),
                )

        self.journal_path = Path(journal_path)
        self.journal_writer: JournalWriter | None = None
        if enable_journal:
            self.journal_writer = JournalWriter(
                path=self.journal_path,
                instrument=self.instrument_spec,
            )

        # Restore kill-switch from journal
        now = current_time if current_time is not None else dt.datetime.now(dt.UTC)
        if enable_journal and self.journal_writer is not None:
            self.journal_writer.start()

        past_events = (
            _read_journal_killswitch_events(self.journal_path) if enable_journal else []
        )
        self.killswitch = KillSwitch.restore(
            past_events,
            config=self.risk_config,
            at=now,
            sink=self.journal_writer,
        )

        self._active_listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # State storage
        self._latest_quotes: dict[str, Decimal] = {}
        self._latest_signal: PortfolioAllocationSignal | None = None
        self._account_summary: dict[str, Decimal] = {
            "equity": Decimal("100000.00"),
            "cash": Decimal("100000.00"),
            "buying_power": Decimal("100000.00"),
            "portfolio_value": Decimal("0.00"),
        }
        self._open_positions: list[dict[str, Any]] = []
        self._completed_rebalances: list[dict[str, Any]] = []
        self._recent_orders: list[dict[str, Any]] = []
        self._equity_history: list[dict[str, Any]] = []

        self._high_water_mark: Decimal = Decimal("100000.00")
        self._realized_pnl_today: Decimal = Decimal("0.00")
        self._last_rebalance_time: dt.datetime | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

    def _dispatch_background(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a background coroutine while retaining task references."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return

        task = loop.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe a new WebSocket client queue to receive live updates."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self._active_listeners.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._active_listeners.discard(queue)

    def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Non-blocking broadcast to all connected WebSocket clients."""
        msg = {
            "type": event_type,
            "data": data,
            "timestamp": dt.datetime.now(dt.UTC).isoformat(),
        }
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in list(self._active_listeners):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._active_listeners.discard(q)

    def get_snapshot(self) -> dict[str, Any]:
        """Return the current complete state for REST and WebSocket connections."""
        now = self._current_time or dt.datetime.now(dt.UTC)
        self.killswitch.evaluate(now)

        equity = self._account_summary.get("equity", Decimal("100000.00"))
        cash = self._account_summary.get("cash", Decimal("100000.00"))
        invested = self._account_summary.get("portfolio_value", Decimal("0.00"))

        # Calculate floating PnL across open positions
        unrealized = sum(
            (Decimal(str(p.get("unrealized_pl", "0.00"))) for p in self._open_positions),
            Decimal("0.00"),
        )

        drawdown_pct = Decimal("0.00")
        if self._high_water_mark > Decimal("0"):
            dd = (equity - self._high_water_mark) / self._high_water_mark * Decimal("100")
            drawdown_pct = dd.quantize(Decimal("0.01"))

        # Build detailed holdings list with current vs target weights
        target_weights = (
            self._latest_signal.target_weights if self._latest_signal else {}
        )
        holdings: list[dict[str, Any]] = []

        for p in self._open_positions:
            sym = str(p.get("symbol", ""))
            qty = Decimal(str(p.get("qty", "0")))
            price = self._latest_quotes.get(sym, Decimal(str(p.get("current_price", "0.00"))))
            val = qty * price
            curr_w = ((val / equity) if equity > Decimal("0") else Decimal("0")).quantize(
                Decimal("0.0001")
            )
            tgt_w = target_weights.get(sym, Decimal("0.0000"))
            drift = curr_w - tgt_w

            meta = ETF_METADATA.get(sym, {"name": sym, "category": "ETF"})
            holdings.append(
                {
                    "symbol": sym,
                    "name": meta["name"],
                    "category": meta["category"],
                    "qty": str(qty),
                    "avg_entry_price": str(p.get("avg_entry_price", "0.00")),
                    "current_price": str(price.quantize(Decimal("0.01"))),
                    "market_value": str(val.quantize(Decimal("0.01"))),
                    "current_weight_pct": str((curr_w * Decimal("100")).quantize(Decimal("0.01"))),
                    "target_weight_pct": str((tgt_w * Decimal("100")).quantize(Decimal("0.01"))),
                    "drift_pct": str((drift * Decimal("100")).quantize(Decimal("0.01"))),
                    "unrealized_pl": str(p.get("unrealized_pl", "0.00")),
                }
            )

        # 12-ETF Momentum Radar Scores
        radar_scores = self._latest_signal.to_dict()["scores"] if self._latest_signal else []

        # Next rebalance scheduled calculation (1st day of next month)
        next_month = (now.month % 12) + 1
        next_year = now.year + (1 if now.month == 12 else 0)
        next_rebalance_dt = dt.date(next_year, next_month, 1)

        return {
            "strategy": "VAR_117 Multi-Asset ETF Momentum Rotation",
            "broker_mode": "Alpaca API" if self.broker.is_connected_mode else "Simulated Paper",
            "account_equity": str(equity.quantize(Decimal("0.01"))),
            "account_balance": str(equity.quantize(Decimal("0.01"))),
            "cash_balance": str(cash.quantize(Decimal("0.01"))),
            "invested_value": str(invested.quantize(Decimal("0.01"))),
            "unrealized_pnl": f"{unrealized:+.2f}",
            "realized_pnl_today": f"{self._realized_pnl_today:+.2f}",
            "drawdown_pct": str(drawdown_pct),
            "high_water_mark": str(self._high_water_mark.quantize(Decimal("0.01"))),
            "killswitch": {
                "state": self.killswitch.state.value,
                "is_tripped": self.killswitch.is_tripped,
                "loss_r": str(self.killswitch.loss_r),
                "limit_r": str(self.risk_config.daily_loss_limit_r),
                "trip_reason": self.killswitch.trip_reason,
            },
            "holdings": holdings,
            "open_positions": holdings,  # Backwards compatibility
            "radar": radar_scores,
            "latest_signal": self._latest_signal.to_dict() if self._latest_signal else None,
            "rebalance_info": {
                "last_rebalance_time": self._last_rebalance_time.isoformat()
                if self._last_rebalance_time
                else None,
                "next_rebalance_date": str(next_rebalance_dt),
                "cash_weight_pct": str(

                        (self._latest_signal.cash_weight * Decimal("100")).quantize(Decimal("0.01"))
                        if self._latest_signal
                        else "0.00"

                ),
                "top_assets": self._latest_signal.top_assets if self._latest_signal else [],
                "signal_hash": self._latest_signal.signal_hash if self._latest_signal else None,
            },
            "recent_orders": (
                self._recent_orders if self._recent_orders else self._completed_rebalances[-50:]
            ),
            "completed_trades": (
                self._recent_orders if self._recent_orders else self._completed_rebalances[-50:]
            ),
            "equity_history": self._equity_history[-300:],
            "updated_at": now.isoformat(),
        }

    async def start(self) -> None:
        """Start feed priming, initial allocation signal, and execution loop."""
        if self._running:
            return
        self._running = True

        logger.info("Starting VAR_117 LiveTrader...")

        # Start journal writer thread
        if self.journal_writer is not None and not self.journal_writer.started:
            self.journal_writer.start()

        # Prime feed with historical daily prices
        try:
            prices = self.feed.load_historical_prices(min_bars=self.strategy.min_required_days)
            self._latest_quotes = await self.feed.fetch_latest_quotes()
            self._latest_signal = self.strategy.compute_allocation(prices)
            logger.info(
                "Primed VAR_117 Strategy: Top Assets=%s, Cash Weight=%s",
                self._latest_signal.top_assets,
                self._latest_signal.cash_weight,
            )
        except Exception as exc:
            logger.warning("Could not prime historical prices for VAR_117: %s", exc)

        # Sync account balances and existing open positions from Alpaca
        try:
            self._account_summary = await self.broker.get_account_summary()
            self._high_water_mark = self._account_summary["equity"]
            self._open_positions = await self.broker.get_open_positions()
            self._recent_orders = await self.broker.get_orders(status="all", limit=50)
        except Exception as exc:
            logger.warning("Could not sync account from broker: %s", exc)

        # Launch main background monitoring loop
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

        if self.journal_writer is not None and self.journal_writer.started:
            self.journal_writer.stop()
            self.journal_writer.drain(timeout=dt.timedelta(seconds=2))

        logger.info("VAR_117 LiveTrader stopped.")

    async def execute_rebalance(
        self,
        force: bool = False,
        cooldown_seconds: int = 3600,
    ) -> list[dict[str, Any]]:
        """Trigger systematic rebalance evaluation and order execution."""
        now = self._current_time or dt.datetime.now(dt.UTC)
        self.killswitch.evaluate(now)

        if not force and self._last_rebalance_time is not None:
            elapsed = int((now - self._last_rebalance_time).total_seconds())
            if elapsed < cooldown_seconds:
                remaining_mins = (cooldown_seconds - elapsed) // 60 + 1
                raise RuntimeError(
                    f"Rebalance cooldown active. Please wait {remaining_mins} minutes."
                )

        if self.killswitch.is_tripped:
            logger.warning(
                "Rebalance REFUSED by KillSwitch latch: %s (loss=%sR).",
                self.killswitch.trip_reason,
                self.killswitch.loss_r,
            )
            self.broadcast(
                "killswitch_block",
                {
                    "reason": self.killswitch.trip_reason,
                    "loss_r": str(self.killswitch.loss_r),
                },
            )
            return []

        # Load fresh price history and compute allocation signal
        prices = self.feed.load_historical_prices(min_bars=self.strategy.min_required_days)
        self._latest_quotes = await self.feed.fetch_latest_quotes()
        self._latest_signal = self.strategy.compute_allocation(prices, as_of_date=now.date())

        # Submit delta rebalance orders via AlpacaBroker
        executed_orders = await self.broker.submit_rebalance_orders(
            target_weights=self._latest_signal.target_weights,
            current_prices=self._latest_quotes,
        )

        self._last_rebalance_time = now
        self._completed_rebalances.extend(executed_orders)

        # Refresh positions and balances
        self._open_positions = await self.broker.get_open_positions()
        self._account_summary = await self.broker.get_account_summary()

        rebalance_record = {
            "timestamp": now.isoformat(),
            "signal_hash": self._latest_signal.signal_hash,
            "top_assets": self._latest_signal.top_assets,
            "orders_count": len(executed_orders),
            "orders": executed_orders,
        }
        self.broadcast("rebalance_executed", rebalance_record)

        if self.telegram.is_enabled and executed_orders:
            summary_msg = (
                f"🔄 <b>VAR_117 Rebalance Executed</b>\n"
                f"Top Assets: {', '.join(self._latest_signal.top_assets)}\n"
                f"Orders: {len(executed_orders)} filled\n"
                f"Equity: ${self._account_summary['equity']}"
            )
            self._dispatch_background(self.telegram.send_message(summary_msg))

        return executed_orders

    async def _run_loop(self) -> None:
        """Periodic monitoring loop updating quotes, portfolio NAV, and risk checks."""
        while self._running:
            try:
                # 1. Update live quotes
                quotes = await self.feed.fetch_latest_quotes()
                self._latest_quotes.update(quotes)

                # 2. Update open positions and calculate total equity
                self._open_positions = await self.broker.get_open_positions()
                acc = await self.broker.get_account_summary()
                self._account_summary.update(acc)
                with contextlib.suppress(Exception):
                    broker_orders = await self.broker.get_orders(status="all", limit=50)
                    if broker_orders:
                        self._recent_orders = broker_orders

                equity = acc["equity"]
                if equity > self._high_water_mark:
                    self._high_water_mark = equity

                # 3. Check drawdown and killswitch
                now = self._current_time or dt.datetime.now(dt.UTC)
                if self._high_water_mark > Decimal("0"):
                    drawdown = (equity - self._high_water_mark) / self._high_water_mark
                    # If portfolio drawdown exceeds 10%, trip killswitch
                    if drawdown <= Decimal("-0.10"):
                        risk_amt = self.risk_config.risk_amount(self._high_water_mark)
                        loss = self._high_water_mark - equity
                        state = self.killswitch.record_trade(-loss, risk_amount=risk_amt, at=now)
                        if state is KillSwitchState.TRIPPED:
                            logger.warning(
                                "PORTFOLIO KILLSWITCH TRIPPED: Drawdown exceeds 10% (%s).",
                                drawdown,
                            )
                            self.broadcast(
                                "killswitch_tripped",
                                {
                                    "reason": "Portfolio drawdown exceeded 10%",
                                    "loss_r": str(self.killswitch.loss_r),
                                },
                            )

                # 4. Record equity curve snapshot for chart
                now_ts = int(now.timestamp())
                self._equity_history.append(
                    {
                        "time": now_ts,
                        "equity": float(equity),
                        "cash": float(acc["cash"]),
                    }
                )
                if len(self._equity_history) > 300:
                    self._equity_history.pop(0)

                # 5. Broadcast live portfolio snapshot to WebSockets
                self.broadcast(
                    "portfolio_update",
                    {
                        "equity": str(equity.quantize(Decimal("0.01"))),
                        "cash": str(acc["cash"].quantize(Decimal("0.01"))),
                        "invested": str(acc["portfolio_value"].quantize(Decimal("0.01"))),
                        "drawdown_pct": str(
                            (
                                (
                                    (equity - self._high_water_mark)
                                    / self._high_water_mark
                                    * Decimal("100")
                                )
                                if self._high_water_mark > Decimal("0")
                                else Decimal("0.00")
                            ).quantize(Decimal("0.01"))
                        ),
                        "time": now_ts,
                    },
                )

            except Exception as exc:
                logger.error("Error in LiveTrader monitoring loop: %s", exc)

            await asyncio.sleep(3)


# Singleton live trader instance used by FastAPI lifespan
global_trader = LiveTrader()
