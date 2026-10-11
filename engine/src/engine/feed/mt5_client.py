"""Real-time market data feed from MetaTrader 5.

Connects to the running desktop MetaTrader 5 terminal, auto-resolves symbol
variants (e.g. ``XAUUSD`` vs ``XAUUSDm`` on Exness), fetches historical seed bars,
and streams live quotes into the engine's aggregator and strategy layer.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import MetaTrader5 as mt5

from engine.market.models import OHLC, Bar, Quote, Timeframe

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

__all__ = [
    "MT5Feed",
    "MT5FeedError",
]


class MT5FeedError(RuntimeError):
    """Raised when MT5 cannot connect or retrieve data."""


class MT5Feed:
    """Consumes real-time ticks and historical bars directly from MetaTrader 5."""

    def __init__(self, requested_symbol: str = "XAUUSD") -> None:
        self._requested_symbol = requested_symbol
        self._symbol: str | None = None
        self._initialized = False

    def connect(self) -> None:
        """Initialize the connection to the running MT5 terminal."""
        if not mt5.initialize():
            err = mt5.last_error()
            raise MT5FeedError(f"Failed to initialize MetaTrader 5: {err}")
        self._initialized = True
        self._symbol = self.resolve_symbol(self._requested_symbol)
        logger.info("MT5Feed connected successfully. Active symbol: %s", self._symbol)

    def resolve_symbol(self, base_symbol: str) -> str:
        """Find the exact tradable symbol in MT5 (e.g. XAUUSD vs XAUUSDm on Exness)."""
        candidates = [
            base_symbol,
            f"{base_symbol}m",   # Exness Standard
            f"{base_symbol}c",   # Exness Cent
            f"{base_symbol}z",   # Exness Zero
            f"{base_symbol}r",   # Exness Raw Spread
            f"{base_symbol}.s",
            "GOLD",
        ]
        for candidate in candidates:
            info = mt5.symbol_info(candidate)
            if info is not None:
                if not info.visible:
                    mt5.symbol_select(candidate, True)
                return candidate

        all_symbols = [s.name for s in (mt5.symbols_get() or []) if "XAU" in s.name or "GOLD" in s.name]
        if all_symbols:
            chosen = all_symbols[0]
            mt5.symbol_select(chosen, True)
            return chosen

        raise MT5FeedError(f"Symbol {base_symbol} (or variants) not found in MT5 Market Watch.")

    @property
    def symbol(self) -> str:
        if self._symbol is None:
            self.connect()
        assert self._symbol is not None
        return self._symbol

    def get_account_summary(self) -> dict[str, Any]:
        """Fetch current balance, equity, leverage and currency from MT5."""
        if not self._initialized:
            self.connect()
        acc = mt5.account_info()
        if acc is None:
            raise MT5FeedError(f"Failed to get account info: {mt5.last_error()}")
        return {
            "login": acc.login,
            "name": acc.name,
            "server": acc.server,
            "balance": str(acc.balance),
            "equity": str(acc.equity),
            "margin": str(acc.margin),
            "free_margin": str(acc.margin_free),
            "profit": str(acc.profit),
            "leverage": acc.leverage,
            "currency": acc.currency,
        }

    def fetch_historical_bars(self, timeframe: Timeframe, count: int = 100) -> list[Bar]:
        """Load recent completed bars from MT5 to prime the strategy indicators."""
        if not self._initialized:
            self.connect()

        tf_str = timeframe.value if hasattr(timeframe, "value") else str(timeframe)
        mt5_tf = mt5.TIMEFRAME_M1 if tf_str == "M1" else mt5.TIMEFRAME_M5
        rates = mt5.copy_rates_from_pos(self.symbol, mt5_tf, 0, count)
        if rates is None or len(rates) == 0:
            logger.warning("No historical rates returned from MT5 for %s", self.symbol)
            return []

        bars: list[Bar] = []
        for r in rates:
            # Rates in MT5: time, open, high, low, close, tick_volume, spread, real_volume
            # Prices are float from MT5, format with 3 decimals for gold
            stamp = dt.datetime.fromtimestamp(r["time"], dt.UTC)
            o = Decimal(f"{r['open']:.3f}")
            h = Decimal(f"{r['high']:.3f}")
            l = Decimal(f"{r['low']:.3f}")
            c = Decimal(f"{r['close']:.3f}")
            half_spread = Decimal(f"{(r['spread'] * 0.01) / 2:.3f}") if r["spread"] > 0 else Decimal("0.05")

            bid_ohlc = OHLC(open=o - half_spread, high=h - half_spread, low=l - half_spread, close=c - half_spread)
            ask_ohlc = OHLC(open=o + half_spread, high=h + half_spread, low=l + half_spread, close=c + half_spread)

            bars.append(
                Bar(
                    timeframe=tf_str,
                    timestamp_utc=stamp,
                    complete=True,
                    volume=int(r["tick_volume"]),
                    bid_ohlc=bid_ohlc,
                    ask_ohlc=ask_ohlc,
                )
            )
        return bars

    def get_latest_quote(self) -> Quote | None:
        """Fetch the most recent bid/ask tick as a domain Quote."""
        if not self._initialized:
            self.connect()
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None or tick.bid <= 0 or tick.ask <= 0:
            return None

        stamp = dt.datetime.fromtimestamp(tick.time, dt.UTC)
        bid = Decimal(f"{tick.bid:.3f}")
        ask = Decimal(f"{tick.ask:.3f}")
        return Quote(bid=bid, ask=ask, timestamp_utc=stamp)

    async def stream_quotes(
        self,
        poll_interval: float = 0.05,
    ) -> AsyncIterator[Quote]:
        """Asynchronously yield live price quotes as they change in MT5."""
        if not self._initialized:
            self.connect()

        last_msc = 0
        last_bid: Decimal | None = None
        last_ask: Decimal | None = None

        while True:
            try:
                tick = mt5.symbol_info_tick(self.symbol)
                if tick is not None and tick.bid > 0 and tick.ask > 0:
                    current_msc = getattr(tick, "time_msc", int(tick.time * 1000))
                    bid = Decimal(f"{tick.bid:.3f}")
                    ask = Decimal(f"{tick.ask:.3f}")

                    # Yield if time advanced or bid/ask updated
                    if current_msc != last_msc or bid != last_bid or ask != last_ask:
                        last_msc = current_msc
                        last_bid = bid
                        last_ask = ask
                        stamp = dt.datetime.fromtimestamp(tick.time, dt.UTC)
                        yield Quote(bid=bid, ask=ask, timestamp_utc=stamp)
            except Exception as exc:
                logger.error("Error reading MT5 tick: %s", exc)

            await asyncio.sleep(poll_interval)
