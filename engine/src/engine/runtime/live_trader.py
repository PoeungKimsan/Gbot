"""Live Trading Engine for MetaTrader 5 and ICT Strategies.

Bridges real-time MT5 market data, bar aggregation, ICT Silver Bullet & Asian
Judas Swing strategies, MT5 order execution with attached SL/TP, and real-time
WebSocket streaming for the live web dashboard.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from engine.execution.mt5_broker import MT5Broker, OrderSide
from engine.feed.mt5_client import MT5Feed
from engine.market.builder import BarAggregator, BarBuilder
from engine.market.models import Bar, Quote, Timeframe
from engine.strategy.judas_swing import JudasSwingStrategy

logger = logging.getLogger(__name__)

__all__ = [
    "LiveTrader",
    "TraderSnapshot",
    "global_trader",
]


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
    open_positions: list[dict[str, Any]]
    completed_trades: list[dict[str, Any]]
    recent_bars: list[dict[str, Any]]
    asian_range: dict[str, Any] | None
    latest_signal: dict[str, Any] | None
    updated_at: str


class LiveTrader:
    """Orchestrates live MT5 streaming, ICT strategies, and UI WebSocket dispatch."""

    def __init__(
        self,
        symbol: str = "XAUUSDm",
        lot_size: Decimal = Decimal("0.01"),
        paper_mode: bool = False,
    ) -> None:
        self.symbol = symbol
        self.lot_size = lot_size
        self.paper_mode = paper_mode

        self.feed = MT5Feed(requested_symbol=symbol)
        self.broker = MT5Broker(symbol=symbol, paper_mode=paper_mode)
        self.judas_strategy = JudasSwingStrategy()

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
                # Remove stale / slow client
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
            try:
                unrealized += Decimal(str(pos.get("profit", "0")))
            except Exception:
                pass

        account = {}
        try:
            account = self.feed.get_account_summary()
        except Exception:
            pass

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
            for b in self._recent_m5_bars[-120:]
        ]

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
            "open_positions": self._open_positions,
            "completed_trades": self._completed_trades,
            "recent_bars": bars_json,
            "asian_range": asian,
            "latest_signal": self._latest_signal,
            "updated_at": dt.datetime.now(dt.UTC).isoformat(),
        }

    async def start(self) -> None:
        """Start the live market data feed and strategy worker."""
        if self._running:
            return
        self._running = True

        logger.info("Starting LiveTrader for %s...", self.symbol)
        self.feed.connect()
        self.symbol = self.feed.symbol
        self.broker.symbol = self.symbol

        # Seed with historical M5 bars so the chart has history instantly
        try:
            seed_bars = self.feed.fetch_historical_bars(Timeframe.M5, count=120)
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
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("LiveTrader stopped.")

    async def _run_loop(self) -> None:
        """Main execution loop consuming real-time MT5 ticks."""
        poll_count = 0
        async for quote in self.feed.stream_quotes(poll_interval=0.08):
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

    def _refresh_positions(self) -> None:
        """Sync open positions and floating PnL from MT5."""
        try:
            self._open_positions = self.broker.get_open_positions()
            # Broadcast position updates
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
        """Handle new closed 5-minute candle and evaluate strategy signals."""
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

            # Execute via MT5Broker
            try:
                side = OrderSide.BUY if judas_setup.side.value == "BUY" else OrderSide.SELL
                res = self.broker.place_market_order(
                    side=side,
                    quantity=self.lot_size,
                    stop_loss=judas_setup.stop_loss,
                    take_profit=judas_setup.take_profit,
                    comment="ICT Judas",
                )
                if res.success:
                    self._completed_trades.append(
                        {
                            "id": res.order_id,
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
            except Exception as exc:
                logger.error("Failed to execute order: %s", exc)


# Global singleton instance for FastAPI to share with runtime
global_trader = LiveTrader()
