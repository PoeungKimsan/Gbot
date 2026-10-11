"""Live Trading Engine for MetaTrader 5 and ICT Strategies.

Bridges real-time MT5 market data, bar aggregation, ICT Asian Judas Swing strategy,
MT5 order execution with attached SL/TP, Decimal risk controls with latching
daily-loss killswitch and hash-chained SQLite journal persistence, and real-time
WebSocket streaming for the live web dashboard.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import sqlite3
from collections.abc import Coroutine
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from engine.domain.events import DomainEvent, EventType
from engine.execution.mt5_broker import MT5Broker, OrderSide
from engine.feed.mt5_client import MT5Feed
from engine.journal.writer import JournalWriter
from engine.market.builder import BarAggregator, BarBuilder
from engine.market.instrument import InstrumentSpec
from engine.market.models import Bar, Quote, Timeframe
from engine.risk.config import RiskConfig, load_risk_config
from engine.risk.killswitch import KillSwitch, KillSwitchState
from engine.risk.rounding import floor_to_step
from engine.runtime.telegram import TelegramNotifier
from engine.strategy.judas_swing import JudasSwingStrategy

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_INSTRUMENT",
    "LiveTrader",
    "TraderSnapshot",
    "global_trader",
]

DEFAULT_INSTRUMENT = InstrumentSpec(
    name="XAU_USD",
    pip_location=-2,
    display_precision=2,
    trade_units_precision=2,
    minimum_trade_size=Decimal("0.01"),
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


@dataclass
class TraderSnapshot:
    symbol: str
    bid: str
    ask: str
    mid: str
    spread_ticks: str
    daily_change_pct: str
    daily_high: str
    daily_low: str
    account_balance: str
    account_equity: str
    unrealized_pnl: str
    realized_pnl_today: str
    killswitch_state: str
    killswitch_tripped: bool
    killswitch_loss_r: str
    killswitch_limit_r: str
    open_positions: list[dict[str, Any]]
    completed_trades: list[dict[str, Any]]
    recent_bars: list[dict[str, Any]]
    asian_range: dict[str, Any] | None
    latest_signal: dict[str, Any] | None
    updated_at: str


class LiveTrader:
    """Orchestrates live MT5 streaming, ICT strategies, kill-switch, and WebSocket dispatch."""

    def __init__(
        self,
        symbol: str = "XAUUSDm",
        lot_size: Decimal | None = None,
        paper_mode: bool = False,
        risk_config: RiskConfig | None = None,
        risk_config_path: Path | str = "config/risk.yaml",
        journal_path: Path | str = "journal.db",
        enable_journal: bool = True,
        feed: MT5Feed | None = None,
        broker: MT5Broker | None = None,
        current_time: dt.datetime | None = None,
        telegram: TelegramNotifier | None = None,
    ) -> None:
        self.symbol = symbol
        self.lot_size = lot_size
        self.paper_mode = paper_mode
        self.instrument_spec = DEFAULT_INSTRUMENT
        self._current_time = current_time
        self.telegram = telegram if telegram is not None else TelegramNotifier()

        # Feed & Broker
        self.feed = feed if feed is not None else MT5Feed(requested_symbol=symbol)
        self.broker = (
            broker
            if broker is not None
            else MT5Broker(symbol=symbol, paper_mode=paper_mode)
        )
        self.judas_strategy = JudasSwingStrategy()

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

        self.builder: BarBuilder | None = None
        self.aggregator: BarAggregator | None = None

        self._active_listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._running = False
        self._task: asyncio.Task[None] | None = None

        self._latest_quote: Quote | None = None
        self._recent_m5_bars: list[Bar] = []
        self._open_positions: list[dict[str, Any]] = []
        self._completed_trades: list[dict[str, Any]] = []
        self._latest_signal: dict[str, Any] | None = None

        self._daily_open_price: Decimal | None = None
        self._daily_high: Decimal | None = None
        self._daily_low: Decimal | None = None
        self._realized_pnl_today: Decimal = Decimal("0.00")
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
        """Subscribe a new WebSocket client queue to receive live ticks and updates."""
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
        for q in self._active_listeners:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._active_listeners.discard(q)

    def get_snapshot(self) -> dict[str, Any]:
        """Return the current complete state for REST and new WebSocket connections."""
        bid = str(self._latest_quote.bid) if self._latest_quote else "0.00"
        ask = str(self._latest_quote.ask) if self._latest_quote else "0.00"
        mid = (
            str((self._latest_quote.bid + self._latest_quote.ask) / 2)
            if self._latest_quote
            else "0.00"
        )
        spread_ticks = (
            str(int((self._latest_quote.ask - self._latest_quote.bid) * 100))
            if self._latest_quote
            else "0"
        )

        daily_change = "0.00"
        if self._latest_quote and self._daily_open_price and self._daily_open_price > 0:
            current_mid = (self._latest_quote.bid + self._latest_quote.ask) / 2
            diff = ((current_mid - self._daily_open_price) / self._daily_open_price) * 100
            daily_change = f"{diff:+.2f}"

        # Calculate floating PnL
        unrealized = Decimal("0.00")
        for pos in self._open_positions:
            with contextlib.suppress(Exception):
                unrealized += Decimal(str(pos.get("profit", "0")))

        account: dict[str, Any] = {}
        with contextlib.suppress(Exception):
            account = self.feed.get_account_summary()

        asian = None
        if self.judas_strategy.current_asian_range:
            ar = self.judas_strategy.current_asian_range
            asian = {
                "date": str(ar.date),
                "high": str(ar.high),
                "low": str(ar.low),
                "complete": ar.complete,
            }

        bars_json = [
            {
                "time": int(b.timestamp_utc.timestamp()),
                "open": float(b.mid_ohlc.open),
                "high": float(b.mid_ohlc.high),
                "low": float(b.mid_ohlc.low),
                "close": float(b.mid_ohlc.close),
                "volume": b.volume,
            }
            for b in self._recent_m5_bars[-300:]
        ]

        now = self._current_time or dt.datetime.now(dt.UTC)
        self.killswitch.evaluate(now)

        return {
            "symbol": self.symbol,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "spread_ticks": spread_ticks,
            "daily_change_pct": daily_change,
            "daily_high": str(self._daily_high or mid),
            "daily_low": str(self._daily_low or mid),
            "account_balance": account.get("balance", "1000.00"),
            "account_equity": account.get("equity", "1000.00"),
            "unrealized_pnl": f"{unrealized:+.2f}",
            "realized_pnl_today": f"{self._realized_pnl_today:+.2f}",
            "killswitch": {
                "state": self.killswitch.state.value,
                "is_tripped": self.killswitch.is_tripped,
                "loss_r": str(self.killswitch.loss_r),
                "limit_r": str(self.risk_config.daily_loss_limit_r),
                "trip_reason": self.killswitch.trip_reason,
            },
            "open_positions": self._open_positions,
            "completed_trades": self._completed_trades,
            "recent_bars": bars_json,
            "asian_range": asian,
            "latest_signal": self._latest_signal,
            "updated_at": now.isoformat(),
        }

    async def start(self) -> None:
        """Start the live market data feed, journal worker, and strategy worker."""
        if self._running:
            return
        self._running = True

        logger.info("Starting LiveTrader for %s...", self.symbol)

        # Start journal writer thread if enabled
        if self.journal_writer is not None and not self.journal_writer.started:
            self.journal_writer.start()

        self.feed.connect()
        self.symbol = self.feed.symbol
        self.broker.symbol = self.symbol

        # Seed with historical M5 bars so the chart has history instantly and the strategy is primed
        try:
            seed_bars = self.feed.fetch_historical_bars(Timeframe.M5, count=300)
            self._recent_m5_bars = list(seed_bars)
            if seed_bars:
                self._daily_open_price = seed_bars[0].mid_ohlc.open
                self._daily_high = max(b.mid_ohlc.high for b in seed_bars)
                self._daily_low = min(b.mid_ohlc.low for b in seed_bars)
                # Prime the strategy with recent bars
                for b in seed_bars:
                    self.judas_strategy.on_bar(b)
            logger.info("Primed LiveTrader with %d historical M5 bars", len(seed_bars))
        except Exception as exc:
            logger.warning("Could not pre-seed historical bars: %s", exc)

        # Setup Bar Builders
        self.builder = BarBuilder(timeframe=Timeframe.M1)
        self.aggregator = BarAggregator(source=Timeframe.M1, target=Timeframe.M5)

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

        logger.info("LiveTrader stopped.")

    async def _run_loop(self) -> None:
        """Main execution loop consuming real-time MT5 ticks."""
        poll_count = 0
        async for quote in self.feed.stream_quotes(poll_interval=Decimal("0.08")):
            if not self._running:
                break

            self._latest_quote = quote
            mid = (quote.bid + quote.ask) / 2

            # Daily high/low tracking
            if self._daily_high is None or mid > self._daily_high:
                self._daily_high = mid
            if self._daily_low is None or mid < self._daily_low:
                self._daily_low = mid
            if self._daily_open_price is None:
                self._daily_open_price = mid

            # Broadcast live tick to WebSockets
            spread_ticks = int((quote.ask - quote.bid) * 100)
            self.broadcast(
                "tick",
                {
                    "bid": str(quote.bid),
                    "ask": str(quote.ask),
                    "mid": str(mid),
                    "spread_ticks": spread_ticks,
                    "time": int(quote.timestamp_utc.timestamp()),
                },
            )

            # Build M1 bars from quotes
            if self.builder and self.aggregator:
                for minute_bar in self.builder.add_quote(quote):
                    for m5_bar in self.aggregator.add(minute_bar):
                        self._on_m5_bar_closed(m5_bar)

            # Periodically update positions (every ~1s)
            poll_count += 1
            if poll_count % 12 == 0:
                self._refresh_positions()

    def record_trade_result(self, pnl: Decimal, close_time: dt.datetime | None = None) -> None:
        """Record a closed trade PnL, updating the daily tracker and kill-switch."""
        at = close_time or dt.datetime.now(dt.UTC)
        self._realized_pnl_today += pnl

        equity = Decimal("1000.00")
        with contextlib.suppress(Exception):
            acc = self.feed.get_account_summary()
            equity = Decimal(str(acc.get("equity", "1000.00")))

        risk_amount = self.risk_config.risk_amount(equity)
        state = self.killswitch.record_trade(pnl, risk_amount=risk_amount, at=at)

        if state is KillSwitchState.TRIPPED:
            logger.warning(
                "KILL-SWITCH TRIPPED on realized loss: %s (loss_r=%s). All new entries blocked.",
                self.killswitch.trip_reason,
                self.killswitch.loss_r,
            )
            self.broadcast(
                "killswitch_tripped",
                {
                    "reason": self.killswitch.trip_reason,
                    "loss_r": str(self.killswitch.loss_r),
                    "limit_r": str(self.risk_config.daily_loss_limit_r),
                },
            )
            if self.telegram.is_enabled:
                self._dispatch_background(
                    self.telegram.notify_killswitch_tripped(
                        reason=self.killswitch.trip_reason,
                        loss_r=self.killswitch.loss_r,
                        limit_r=self.risk_config.daily_loss_limit_r,
                    )
                )

    def calculate_lot_size(
        self,
        entry: Decimal,
        stop_loss: Decimal,
        equity: Decimal,
    ) -> Decimal:
        """Calculate dynamic lot size from risk budget and MT5 gold contract size."""
        stop_distance = abs(entry - stop_loss)
        if stop_distance <= Decimal("0"):
            return self.lot_size if self.lot_size is not None else Decimal("0.01")

        try:
            # 1 standard lot = 100 troy oz on MT5 XAUUSD.
            contract_size = Decimal("100")
            risk_amount = self.risk_config.risk_amount(equity)
            dollar_risk_per_lot = stop_distance * contract_size
            if dollar_risk_per_lot <= Decimal("0"):
                return Decimal("0")

            raw_lots = floor_to_step(risk_amount / dollar_risk_per_lot, precision=2)
            if raw_lots < Decimal("0.01"):
                logger.warning(
                    "Dynamic lot size %s below venue minimum 0.01 (risk=$%s, stop_dist=$%s)",
                    raw_lots,
                    risk_amount,
                    stop_distance,
                )
                return Decimal("0")
            return min(raw_lots, Decimal("10.00"))
        except Exception as exc:
            logger.warning(
                "Error calculating dynamic lot size: %s; falling back to %s",
                exc,
                self.lot_size or Decimal("0.01"),
            )
            return self.lot_size if self.lot_size is not None else Decimal("0.01")

    def _refresh_positions(self) -> None:
        """Sync open positions and floating PnL from MT5."""
        try:
            previous_positions = {p["ticket"]: p for p in self._open_positions}
            self._open_positions = self.broker.get_open_positions()
            current_tickets = {p["ticket"] for p in self._open_positions}

            # Check if any previously tracked position closed
            for ticket, prev_p in previous_positions.items():
                if ticket not in current_tickets:
                    closing_deal = None
                    with contextlib.suppress(Exception):
                        closing_deal = self.broker.get_closed_deal_for_position(ticket)

                    if (
                        isinstance(closing_deal, dict)
                        and isinstance(closing_deal.get("profit"), Decimal)
                    ):
                        closed_profit = closing_deal["profit"]
                        close_time = (
                            closing_deal["time"]
                            if isinstance(closing_deal.get("time"), dt.datetime)
                            else dt.datetime.now(dt.UTC)
                        )
                        exit_price = (
                            closing_deal["price"]
                            if isinstance(closing_deal.get("price"), Decimal)
                            else Decimal(str(prev_p.get("current_price", "0.000")))
                        )
                    else:
                        closed_profit = Decimal(str(prev_p.get("profit", "0.00")))
                        close_time = dt.datetime.now(dt.UTC)
                        exit_price = Decimal(str(prev_p.get("current_price", "0.000")))

                    self.record_trade_result(closed_profit, close_time=close_time)

                    # Update trade record in _completed_trades
                    matched = False
                    for trade in self._completed_trades:
                        if trade.get("id") == ticket or trade.get("ticket") == ticket:
                            trade["status"] = "CLOSED"
                            trade["exit"] = str(exit_price)
                            trade["profit"] = f"{closed_profit:+.2f}"
                            trade["closed_at"] = close_time.isoformat()
                            matched = True
                            break
                    if not matched:
                        self._completed_trades.append(
                            {
                                "id": ticket,
                                "ticket": ticket,
                                "side": prev_p.get("side", "BUY"),
                                "volume": prev_p.get("volume", "0.01"),
                                "entry": prev_p.get("open_price", "0.000"),
                                "exit": str(exit_price),
                                "sl": prev_p.get("sl"),
                                "tp": prev_p.get("tp"),
                                "profit": f"{closed_profit:+.2f}",
                                "status": "CLOSED",
                                "time": prev_p.get("time", close_time.isoformat()),
                                "closed_at": close_time.isoformat(),
                                "strategy": "ICT Judas",
                            }
                        )

                    self.broadcast(
                        "trade_closed",
                        {
                            "ticket": ticket,
                            "profit": str(closed_profit),
                            "exit_price": str(exit_price),
                            "time": close_time.isoformat(),
                        },
                    )
                    if self.telegram.is_enabled:
                        self._dispatch_background(
                            self.telegram.notify_order_closed(
                                ticket=ticket,
                                symbol=self.symbol,
                                exit_price=exit_price,
                                profit=closed_profit,
                                daily_pnl=self._realized_pnl_today,
                                close_time=close_time,
                            )
                        )

            unrealized = sum((Decimal(p["profit"]) for p in self._open_positions), Decimal("0.00"))
            self.broadcast(
                "positions",
                {
                    "positions": self._open_positions,
                    "unrealized_pnl": f"{unrealized:+.2f}",
                },
            )
        except Exception as exc:
            logger.error("Error refreshing positions: %s", exc)

    def _on_m5_bar_closed(self, bar: Bar) -> None:
        """Handle new closed 5-minute candle, evaluate strategy, and check kill-switch."""
        self._recent_m5_bars.append(bar)
        if len(self._recent_m5_bars) > 300:
            self._recent_m5_bars.pop(0)

        # Broadcast completed bar to chart
        self.broadcast(
            "bar",
            {
                "time": int(bar.timestamp_utc.timestamp()),
                "open": float(bar.mid_ohlc.open),
                "high": float(bar.mid_ohlc.high),
                "low": float(bar.mid_ohlc.low),
                "close": float(bar.mid_ohlc.close),
                "volume": bar.volume,
            },
        )

        # Evaluate ICT Judas Swing setup
        judas_setup = self.judas_strategy.on_bar(bar)
        if judas_setup:
            self._latest_signal = {
                "strategy": "ICT Judas Swing",
                "side": judas_setup.side.value,
                "entry": str(judas_setup.entry_price),
                "sl": str(judas_setup.stop_loss),
                "tp": str(judas_setup.take_profit),
                "time": judas_setup.bar_time.isoformat(),
                "reason": judas_setup.reason,
            }
            logger.info("EXECUTION TRIGGER: %s", self._latest_signal)
            self.broadcast("signal", self._latest_signal)

            # Check Kill-Switch latch before any order placement
            self.killswitch.evaluate(bar.timestamp_utc)
            if self.killswitch.is_tripped:
                logger.warning(
                    "Signal REFUSED by KillSwitch latch: %s (loss=%sR, limit=%sR)",
                    self.killswitch.trip_reason,
                    self.killswitch.loss_r,
                    self.risk_config.daily_loss_limit_r,
                )
                self.broadcast(
                    "killswitch_block",
                    {
                        "reason": self.killswitch.trip_reason,
                        "loss_r": str(self.killswitch.loss_r),
                        "limit_r": str(self.risk_config.daily_loss_limit_r),
                        "signal": self._latest_signal,
                    },
                )
                return

            # Determine order volume: fixed lot_size if set, otherwise dynamic risk sizing
            quantity = self.lot_size
            if quantity is None:
                equity = Decimal("1000.00")
                with contextlib.suppress(Exception):
                    acc = self.feed.get_account_summary()
                    equity = Decimal(str(acc.get("equity", "1000.00")))
                quantity = self.calculate_lot_size(
                    entry=judas_setup.entry_price,
                    stop_loss=judas_setup.stop_loss,
                    equity=equity,
                )
                if quantity <= Decimal("0"):
                    logger.warning("Order REFUSED: position size is below venue minimum trade size")
                    return

            # Execute via MT5Broker with mandatory Stop Loss & Take Profit
            try:
                side = OrderSide.BUY if judas_setup.side.value == "BUY" else OrderSide.SELL
                res = self.broker.place_market_order(
                    side=side,
                    quantity=quantity,
                    stop_loss=judas_setup.stop_loss,
                    take_profit=judas_setup.take_profit,
                    comment="ICT Judas",
                )
                if res.success:
                    self._completed_trades.append(
                        {
                            "id": res.order_id,
                            "ticket": res.order_id,
                            "side": judas_setup.side.value,
                            "volume": str(res.volume),
                            "entry": str(res.price),
                            "sl": str(judas_setup.stop_loss),
                            "tp": str(judas_setup.take_profit),
                            "time": dt.datetime.now(dt.UTC).isoformat(),
                            "status": "OPEN",
                            "strategy": "ICT Judas",
                        }
                    )
                    if self.telegram.is_enabled:
                        self._dispatch_background(
                            self.telegram.notify_order_opened(
                                order_id=res.order_id,
                                side=judas_setup.side.value,
                                volume=res.volume,
                                entry_price=res.price,
                                stop_loss=judas_setup.stop_loss,
                                take_profit=judas_setup.take_profit,
                                symbol=self.symbol,
                                strategy="ICT Judas Swing",
                                reason=judas_setup.reason,
                            )
                        )
            except Exception as exc:
                logger.error("Failed to execute order: %s", exc)


# Global singleton instance for FastAPI to share with runtime
global_trader = LiveTrader()
