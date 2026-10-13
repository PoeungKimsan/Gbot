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
import uuid
from collections.abc import Coroutine
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from engine.domain.events import DomainEvent, EventType
from engine.execution.alpaca_broker import AlpacaBroker
from engine.feed.alpaca_feed import AlpacaFeed, Quote
from engine.feed.refresh_data import get_manifest
from engine.journal.writer import JournalWriter
from engine.market.instrument import InstrumentSpec
from engine.market.nyse_calendar import (
    is_last_trading_day_of_month,
    is_nyse_trading_day,
    last_completed_session,
    next_trading_day,
)
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
    "EXEC_WINDOW_END",
    "EXEC_WINDOW_START",
    "ExecutionRefused",
    "LiveTrader",
    "SignalRefused",
    "global_trader",
    "run_oneshot_rebalance",
]

NEW_YORK = ZoneInfo("America/New_York")
EXEC_WINDOW_START = dt.time(15, 50)
EXEC_WINDOW_END = dt.time(15, 58)


class ExecutionRefused(ValueError, RuntimeError):  # noqa: N818
    """Raised when rebalance execution is refused by schedule, calendar, or risk guards."""


class SignalRefused(ValueError, RuntimeError):  # noqa: N818
    """Raised when signal generation is refused by calendar or data validation."""


DEFAULT_INSTRUMENT = InstrumentSpec(
    name="US_ETF_PORTFOLIO",
    pip_location=-2,
    display_precision=2,
    trade_units_precision=4,
    minimum_trade_size=Decimal("0.0001"),
)


def _get_current_time(
    provided: dt.datetime | None = None,
) -> tuple[dt.datetime, dt.datetime, dt.date]:
    """Return (now_ny, now_utc, as_of_date) with America/New_York session alignment."""
    if provided is not None:
        ny = (
            provided.astimezone(NEW_YORK)
            if provided.tzinfo
            else provided.replace(tzinfo=NEW_YORK)
        )
        utc = ny.astimezone(dt.UTC)
        return ny, utc, ny.date()
    ny = dt.datetime.now(NEW_YORK)
    utc = ny.astimezone(dt.UTC)
    return ny, utc, ny.date()


def _read_journal_killswitch_events(path: Path) -> list[DomainEvent]:
    """Replay kill-switch transitions from SQLite journal to restore latch across restarts."""
    if not path.is_file():
        return []

    events: list[DomainEvent] = []
    try:
        conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        with conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='journal'"
            )
            if not cur.fetchone():
                return []

            cur.execute(
                "SELECT event_id, event_type, occurred_at, payload FROM journal "
                "WHERE event_type='risk_killswitch' ORDER BY sequence ASC"
            )
            for row in cur.fetchall():
                event_id, event_type, occurred_at_str, payload_raw = row
                parsed = json.loads(payload_raw) if payload_raw else {}
                inner_payload = (
                    parsed.get("payload", parsed)
                    if isinstance(parsed, dict)
                    else {}
                )
                occurred_at = dt.datetime.fromisoformat(occurred_at_str)
                events.append(
                    DomainEvent(
                        event_id=event_id,
                        event_type=EventType(event_type),
                        occurred_at=occurred_at,
                        payload=inner_payload,
                    )
                )
        return events
    except Exception as exc:
        logger.error("Could not read journal events from %s: %s", path, exc)
        raise


class LiveTrader:
    """Orchestrates ETF feed, VAR_117 strategy, Alpaca broker, and WebSocket dispatch."""

    def __init__(
        self,
        feed: AlpacaFeed | None = None,
        broker: AlpacaBroker | None = None,
        strategy: VAR117Strategy | None = None,
        killswitch: KillSwitch | None = None,
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

        self._is_degraded: bool = False
        self._degraded_reason: str | None = None
        self._broker_init_error: Exception | None = None

        # Feed, Strategy, Broker
        if feed is not None:
            self.feed = feed
        else:
            try:
                self.feed = AlpacaFeed()
            except Exception as exc:
                self.feed = None  # type: ignore[assignment]
                self.mark_degraded(f"Feed init failed: {exc}")
        if broker is not None:
            self.broker = broker
        else:
            try:
                self.broker = AlpacaBroker()
            except Exception as exc:
                self.broker = None  # type: ignore[assignment]
                self._broker_init_error = exc
                self.mark_degraded(f"Broker init failed: {exc}")
        self.strategy = strategy if strategy is not None else VAR117Strategy()

        # Risk & Kill-Switch
        if risk_config is not None:
            self.risk_config = risk_config
        else:
            try:
                self.risk_config = load_risk_config(risk_config_path)
            except Exception as exc:
                logger.warning(
                    "Could not load risk config from %s: %s. Using default 10%% limits.",
                    risk_config_path,
                    exc,
                )
                self.risk_config = RiskConfig(
                    max_account_drawdown=Decimal("0.10"),
                    daily_loss_limit_r=Decimal("3.0"),
                    instrument=self.instrument_spec,
                )

        # Restore kill-switch from journal or injectable parameter
        self.journal_path = Path(journal_path)
        self.journal_writer: JournalWriter | None = None
        _, now_utc, _ = _get_current_time(current_time)
        if killswitch is not None:
            self.killswitch = killswitch
        else:
            try:
                past_events = (
                    _read_journal_killswitch_events(self.journal_path) if enable_journal else []
                )
                self.killswitch = KillSwitch.restore(
                    past_events,
                    config=self.risk_config,
                    at=now_utc,
                    sink=None,
                )
            except Exception as exc:
                self.mark_degraded(f"Journal read error: {exc}")
                raise

        # Journal persistence writer
        if enable_journal:
            self.journal_writer = JournalWriter(
                path=self.journal_path,
                instrument=self.instrument_spec,
            )
            self.journal_writer.start()
            self.killswitch._sink = self.journal_writer

        self._active_listeners: set[asyncio.Queue[dict[str, Any]]] = set()
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # State storage
        self._latest_quotes: dict[str, Quote | Decimal] = {}
        self._latest_signal: PortfolioAllocationSignal | None = None
        self._account_summary: dict[str, Decimal] = {
            "equity": Decimal("100000.00"),
            "cash": Decimal("100000.00"),
            "buying_power": Decimal("100000.00"),
            "portfolio_value": Decimal("0.00"),
        }
        self._open_positions: dict[str, dict[str, Decimal]] = {}
        self._completed_rebalances: list[dict[str, Any]] = []
        self._recent_orders: list[dict[str, Any]] = []
        self._equity_history: list[dict[str, Any]] = []

        self._high_water_mark: Decimal = Decimal("100000.00")
        self._realized_pnl_today: Decimal = Decimal("0.00")
        self._last_rebalance_time: dt.datetime | None = None
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @property
    def is_degraded(self) -> bool:
        return self._is_degraded

    @property
    def degraded_reason(self) -> str | None:
        return self._degraded_reason

    def mark_degraded(self, reason: str) -> None:
        self._is_degraded = True
        self._degraded_reason = reason
        logger.warning("LiveTrader marked DEGRADED: %s", reason)

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
        _, now_utc, _ = _get_current_time(self._current_time)
        msg = {
            "type": event_type,
            "data": data,
            "timestamp": now_utc.isoformat(),
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
        now_ny, now_utc, _ = _get_current_time(self._current_time)
        self.killswitch.evaluate(now_utc)

        equity = self._account_summary.get("equity", Decimal("100000.00"))
        cash = self._account_summary.get("cash", Decimal("100000.00"))
        invested = self._account_summary.get("portfolio_value", Decimal("0.00"))

        # Calculate floating PnL across open positions
        unrealized = sum(
            (Decimal(str(p.get("unrealized_pnl", "0.00"))) for p in self._open_positions.values()),
            Decimal("0.00"),
        )

        drawdown_pct = (
            (equity - self._high_water_mark) / self._high_water_mark
            if self._high_water_mark > Decimal("0")
            else Decimal("0.00")
        )

        # Format holdings list
        holdings: list[dict[str, Any]] = []
        for sym, pos in self._open_positions.items():
            qty = pos.get("qty", Decimal("0"))
            avg_p = pos.get("avg_price", Decimal("0"))
            cur_p = pos.get("current_price", avg_p)
            val = pos.get("market_value", qty * cur_p)
            unreal = pos.get("unrealized_pnl", Decimal("0.00"))
            w = (val / equity) if equity > Decimal("0") else Decimal("0.0000")

            meta = ETF_METADATA.get(sym, {})
            holdings.append(
                {
                    "symbol": sym,
                    "name": meta.get("name", sym),
                    "category": meta.get("category", ""),
                    "shares": str(qty),
                    "avg_cost": str(avg_p.quantize(Decimal("0.01"))),
                    "current_price": str(cur_p.quantize(Decimal("0.01"))),
                    "market_value": str(val.quantize(Decimal("0.01"))),
                    "unrealized_pnl": f"{unreal:+.2f}",
                    "current_weight": str(w.quantize(Decimal("0.0001"))),
                    "target_weight": str(
                        self._latest_signal.target_weights.get(sym, Decimal("0.0000"))
                        if self._latest_signal
                        else "0.0000"
                    ),
                }
            )

        # 12-ETF Momentum Radar Scores
        radar_scores = self._latest_signal.to_dict()["scores"] if self._latest_signal else []

        # Next rebalance scheduled calculation (1st day of next month)
        next_month = (now_ny.month % 12) + 1
        next_year = now_ny.year + (1 if now_ny.month == 12 else 0)
        next_rebalance_dt = dt.date(next_year, next_month, 1)

        return {
            "status": "degraded" if self.is_degraded else "healthy",
            "degraded_reason": self.degraded_reason,
            "strategy": "VAR_117 Multi-Asset ETF Momentum Rotation",
            "broker_mode": self.broker.mode.upper() if self.broker is not None else "UNCONFIGURED",
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
            "open_positions": holdings,
            "radar": radar_scores,
            "rebalance_info": {
                "last_rebalanced": (
                    self._last_rebalance_time.isoformat()
                    if self._last_rebalance_time
                    else "Never"
                ),
                "next_scheduled": next_rebalance_dt.isoformat(),
                "rebalance_count": len(self._completed_rebalances),
                "latest_orders": self._recent_orders[:10],
                "signal_hash": self._latest_signal.signal_hash if self._latest_signal else None,
            },
            "recent_orders": self._recent_orders,
            "equity_curve": list(self._equity_history),
        }

    def _journal_strategy_signal(
        self,
        prices: dict[str, list[tuple[dt.date, Decimal]]],
        as_of_date: dt.date,
        occurred_at: dt.datetime,
    ) -> None:
        """Record STRATEGY_SIGNAL domain event into SQLite journal with full audit metadata."""
        if self._latest_signal is None or self.journal_writer is None:
            return

        if not self.journal_writer.started:
            self.journal_writer.start()

        manifest_dict = get_manifest(self.feed.data_dir if self.feed else None)
        per_ticker_last_date = {
            t: prices[t][-1][0].isoformat() for t in self.strategy.universe if t in prices
        }
        per_ticker_mom = {s.ticker: str(s.mom_9m_pct) for s in self._latest_signal.scores}
        per_ticker_vol = {s.ticker: str(s.vol_12m_pct) for s in self._latest_signal.scores}

        signal_payload = {
            "signal_hash": self._latest_signal.signal_hash,
            "as_of_date": as_of_date.isoformat(),
            "manifest": manifest_dict,
            "per_ticker_last_date": per_ticker_last_date,
            "momentum": per_ticker_mom,
            "vol": per_ticker_vol,
            "top_assets": self._latest_signal.top_assets,
            "target_weights": {k: str(v) for k, v in self._latest_signal.target_weights.items()},
            "cash_weight": str(self._latest_signal.cash_weight),
        }
        event = DomainEvent(
            event_id=uuid.uuid4().hex,
            event_type=EventType.STRATEGY_SIGNAL,
            occurred_at=occurred_at,
            payload=signal_payload,
        )
        self.journal_writer.submit(event)

    async def start(self) -> None:
        """Start feed priming, initial allocation signal, and execution loop."""
        if self._running:
            return
        self._running = True

        logger.info("Starting VAR_117 LiveTrader...")

        if self.journal_writer is None:
            self.journal_writer = JournalWriter(
                path=self.journal_path,
                instrument=self.instrument_spec,
            )
            self.killswitch._sink = self.journal_writer
        if not self.journal_writer.started:
            self.journal_writer.start()

        now_ny, now_utc, as_of_date = _get_current_time(self._current_time)

        # Prime feed with historical daily prices
        try:
            if self.feed is None:
                self.feed = AlpacaFeed()
            prices = self.feed.load_historical_prices(
                min_bars=self.strategy.min_required_days,
                expected_as_of_date=as_of_date,
            )
            self._latest_quotes = await self.feed.fetch_latest_quotes(current_time=now_ny)
            self._latest_signal = self.strategy.compute_allocation(prices, as_of_date=as_of_date)
            self._journal_strategy_signal(prices, as_of_date, now_utc)
            logger.info(
                "Primed VAR_117 Strategy: Top Assets=%s, Cash Weight=%s",
                self._latest_signal.top_assets,
                self._latest_signal.cash_weight,
            )
        except Exception as exc:
            self.mark_degraded(f"Feed priming failed: {exc}")
            logger.warning("Could not prime historical prices for VAR_117: %s", exc)

        # Sync account balances and existing open positions from Alpaca
        try:
            if self.broker is None:
                if getattr(self, "_broker_init_error", None) is not None:
                    raise self._broker_init_error
                self.broker = AlpacaBroker()
            self._account_summary = await self.broker.get_account_summary()
            self._high_water_mark = self._account_summary["equity"]
            raw_pos = await self.broker.get_open_positions()
            self._open_positions = {p["symbol"]: p for p in raw_pos}
            self._recent_orders = await self.broker.get_orders(status="all", limit=50)
        except Exception as exc:
            self.mark_degraded(f"Broker account sync failed: {exc}")
            logger.warning("Could not sync account from broker: %s", exc)

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
        if self.is_degraded:
            logger.warning("Rebalance REFUSED: trader is degraded: %s", self.degraded_reason)
            raise RuntimeError(f"Rebalance refused: trader is degraded ({self.degraded_reason})")

        now_ny, now_utc, as_of_date = _get_current_time(self._current_time)
        self.killswitch.evaluate(now_utc)

        if not force and self._last_rebalance_time is not None:
            elapsed = int((now_utc - self._last_rebalance_time).total_seconds())
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

        if self.feed is None:
            self.feed = AlpacaFeed()
        prices = self.feed.load_historical_prices(
            min_bars=self.strategy.min_required_days,
            expected_as_of_date=as_of_date,
        )
        self._latest_quotes = await self.feed.fetch_latest_quotes(current_time=now_ny)
        self._latest_signal = self.strategy.compute_allocation(prices, as_of_date=as_of_date)
        self._journal_strategy_signal(prices, as_of_date, now_utc)

        # Submit delta rebalance orders via AlpacaBroker
        executed_orders = await self.broker.rebalance_portfolio(
            target_weights=self._latest_signal.target_weights,
            current_prices=self._latest_quotes,
            rebalance_date=as_of_date,
        )

        self._last_rebalance_time = now_utc
        self._completed_rebalances.extend(executed_orders)
        self._recent_orders = executed_orders

        raw_pos = await self.broker.get_open_positions()
        self._open_positions = {p["symbol"]: p for p in raw_pos}
        self._account_summary = await self.broker.get_account_summary()

        rebalance_record = {
            "timestamp": now_utc.isoformat(),
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

    def _assert_execute_allowed(
        self,
        signal_date: dt.date,
        now_ny: dt.datetime,
        *,
        bypass_window: bool = False,
        allow_non_month_end: bool = False,
    ) -> None:
        """Validate systematic execution guards before order submission.

        Guards:
        1. Current day must be a scheduled NYSE trading day (blocks weekend/holiday runs).
        2. Current day must be strictly the next scheduled trading day after signal_date
           (blocks same-day and stale runs).
        3. Current time must be within execution window [15:50, 15:58) ET.
        4. Signal date must be the final trading session of the calendar month
           (unless explicitly overridden).
        5. Kill-switch latch must not be tripped.
        """
        today = now_ny.date()
        if not is_nyse_trading_day(today):
            raise ExecutionRefused(f"{today.isoformat()} is not an active NYSE trading session.")

        expected_next = next_trading_day(signal_date)
        if today != expected_next:
            raise ExecutionRefused(
                f"Execution day {today.isoformat()} is not the next scheduled trading session "
                f"({expected_next.isoformat()}) following signal date {signal_date.isoformat()}."
            )

        if not bypass_window and not (EXEC_WINDOW_START <= now_ny.time() < EXEC_WINDOW_END):
            raise ExecutionRefused(
                f"Current time {now_ny.time()} is outside the execution window "
                f"[{EXEC_WINDOW_START}, {EXEC_WINDOW_END}) ET."
            )

        if not allow_non_month_end and not is_last_trading_day_of_month(signal_date):
            raise ExecutionRefused(
                f"Signal date {signal_date.isoformat()} is not the final trading day "
                "of the calendar month."
            )

        utc_time = (
            now_ny.astimezone(dt.UTC)
            if now_ny.tzinfo
            else now_ny.replace(tzinfo=NEW_YORK).astimezone(dt.UTC)
        )
        self.killswitch.evaluate(utc_time)
        if self.killswitch.is_tripped:
            raise ExecutionRefused(
                f"Execution refused: Kill-switch is latched ({self.killswitch.trip_reason})."
            )

    def generate_and_persist_signal(
        self,
        now_ny: dt.datetime | None = None,
        signal_file: Path | str | None = None,
        allow_non_month_end: bool = False,
    ) -> PortfolioAllocationSignal:
        """Signal phase (after close on t): refresh, validate, compute, persist, and journal."""
        ny_time, utc_time, _ = _get_current_time(now_ny or self._current_time)
        as_of = last_completed_session(ny_time)

        if not allow_non_month_end and not is_last_trading_day_of_month(as_of):
            raise SignalRefused(
                f"Session {as_of.isoformat()} is not the final trading day of the calendar month."
            )

        if self.feed is None:
            self.feed = AlpacaFeed()

        prices = self.feed.load_historical_prices(
            min_bars=self.strategy.min_required_days,
            expected_as_of_date=as_of,
        )
        signal = self.strategy.compute_allocation(prices, as_of_date=as_of)
        self._latest_signal = signal
        self._journal_strategy_signal(prices, as_of, utc_time)

        if signal_file:
            out_path = Path(signal_file)
        elif self.feed and self.feed.data_dir:
            out_path = self.feed.data_dir / "latest_signal.json"
        else:
            raise ValueError(
                "No target signal_file path specified and no feed data_dir configured."
            )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(signal.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        tmp_path.replace(out_path)
        logger.info(
            "Persisted VAR_117 signal for %s to %s (hash=%s...)",
            as_of,
            out_path,
            signal.signal_hash[:8],
        )
        return signal

    async def execute_persisted_signal(
        self,
        now_ny: dt.datetime | None = None,
        signal_file: Path | str | None = None,
        bypass_window: bool = False,
        allow_non_month_end: bool = False,
    ) -> list[dict[str, Any]]:
        """Execute phase (next session ~15:50-15:55 ET).

        Loads signal, validates guards, requires signal_date == last completed session, submits.
        """
        if self.is_degraded:
            raise RuntimeError(
                f"Cannot execute persisted signal: engine is degraded ({self.degraded_reason})"
            )

        ny_time, utc_time, _ = _get_current_time(now_ny or self._current_time)
        last_completed = last_completed_session(ny_time)

        if signal_file:
            in_path = Path(signal_file)
        elif self.feed and self.feed.data_dir:
            in_path = self.feed.data_dir / "latest_signal.json"
        else:
            raise FileNotFoundError(
                "No signal file path specified and feed data_dir is not configured."
            )

        if not in_path.is_file():
            raise FileNotFoundError(f"Persisted signal file not found at {in_path}")

        data = json.loads(in_path.read_text(encoding="utf-8"))
        signal_date_str = data.get("as_of_date")
        if not signal_date_str:
            raise ValueError("Persisted signal missing 'as_of_date'")
        signal_date = dt.date.fromisoformat(signal_date_str)

        if signal_date != last_completed:
            raise ExecutionRefused(
                f"Stale signal: signal date {signal_date} does not match "
                f"last completed session {last_completed}."
            )

        self._assert_execute_allowed(
            signal_date=signal_date,
            now_ny=ny_time,
            bypass_window=bypass_window,
            allow_non_month_end=allow_non_month_end,
        )

        raw_weights = data.get("target_weights", {})
        weights = {k: Decimal(str(v)) for k, v in raw_weights.items()}
        invalid_symbols = set(weights.keys()) - set(self.strategy.universe)
        if invalid_symbols:
            raise ValueError(
                "Persisted signal contains invalid symbols not in strategy universe: "
                f"{invalid_symbols}"
            )
        total_w = sum(weights.values())
        if total_w > Decimal("1.0001"):
            raise ValueError(
                f"Persisted target weight sum {total_w} exceeds 1.0001 threshold."
            )

        if self.feed is not None:
            self._latest_quotes = await self.feed.fetch_latest_quotes(current_time=ny_time)

        if self.broker is None:
            if getattr(self, "_broker_init_error", None) is not None:
                raise self._broker_init_error
            self.broker = AlpacaBroker()

        equity = self._account_summary.get("equity", Decimal("100000.00"))
        cash_buffer = max(
            self.risk_config.cash_buffer_min,
            (equity * self.risk_config.cash_buffer_pct).quantize(Decimal("0.01")),
        )

        executed = await self.broker.rebalance_portfolio(
            target_weights=weights,
            current_prices=self._latest_quotes,
            rebalance_date=signal_date,
            cash_buffer=cash_buffer,
            fill_timeout_seconds=self.risk_config.fill_timeout_seconds,
        )
        self._last_rebalance_time = utc_time
        self._completed_rebalances.extend(executed)
        self._recent_orders = executed
        return executed

    async def _run_loop(self) -> None:
        """Periodic monitoring loop updating quotes, portfolio NAV, and risk checks."""
        while self._running:
            try:
                if self.feed is not None:
                    # 1. Update live quotes
                    quotes = await self.feed.fetch_latest_quotes()
                    self._latest_quotes.update(quotes)

                if self.broker is not None:
                    # 2. Update open positions and calculate total equity
                    raw_pos = await self.broker.get_open_positions()
                    self._open_positions = {p["symbol"]: p for p in raw_pos}
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
                _, now_utc, _ = _get_current_time(self._current_time)
                if self._high_water_mark > Decimal("0"):
                    drawdown = (equity - self._high_water_mark) / self._high_water_mark
                    drawdown_breached = (
                        drawdown <= -self.risk_config.max_account_drawdown
                        and not self.killswitch.is_tripped
                    )
                    if drawdown_breached:
                        risk_amt = self.risk_config.risk_amount(self._high_water_mark)
                        loss = self._high_water_mark - equity
                        state = self.killswitch.record_trade(
                            -loss, risk_amount=risk_amt, at=now_utc
                        )
                        if state is KillSwitchState.TRIPPED:
                            logger.warning(
                                "PORTFOLIO KILLSWITCH TRIPPED: Drawdown exceeds %s%% (%s).",
                                self.risk_config.max_account_drawdown * Decimal("100"),
                                drawdown,
                            )
                            reason_msg = (
                                f"Portfolio drawdown exceeded "
                                f"{self.risk_config.max_account_drawdown * Decimal('100')}%"
                            )
                            self.broadcast(
                                "killswitch_tripped",
                                {
                                    "reason": reason_msg,
                                    "loss_r": str(self.killswitch.loss_r),
                                },
                            )

                # 4. Record equity curve snapshot for chart
                now_ts = int(now_utc.timestamp())
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
                                ((equity - self._high_water_mark) / self._high_water_mark)
                                if self._high_water_mark > Decimal("0")
                                else Decimal("0.00")
                            ).quantize(Decimal("0.0001"))
                        ),
                        "high_water_mark": str(self._high_water_mark.quantize(Decimal("0.01"))),
                        "timestamp": now_utc.isoformat(),
                    },
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error in LiveTrader monitoring loop: %s", exc)

            await asyncio.sleep(2)


global_trader: LiveTrader = LiveTrader(enable_journal=False)


def get_trader() -> LiveTrader:
    """Return the global LiveTrader instance."""
    return global_trader


def run_oneshot_rebalance(
    now_ny: dt.datetime | None = None,
    signal_file: Path | str | None = None,
    feed: AlpacaFeed | None = None,
    broker: AlpacaBroker | None = None,
    journal_path: Path | str = "journal.db",
    bypass_window: bool = False,
    allow_non_month_end: bool = False,
) -> int:
    """Scheduled one-shot executor.

    Exits non-zero on any priming, broker-init, journal-read, or stale-data error.
    """
    trader: LiveTrader | None = None
    try:
        trader = LiveTrader(
            feed=feed,
            broker=broker,
            journal_path=journal_path,
            current_time=now_ny,
        )
        if getattr(trader, "_broker_init_error", None) is not None:
            raise trader._broker_init_error
        if trader.is_degraded:
            raise RuntimeError(f"Trader is degraded: {trader.degraded_reason}")
        asyncio.run(
            trader.execute_persisted_signal(
                now_ny=now_ny,
                signal_file=signal_file,
                bypass_window=bypass_window,
                allow_non_month_end=allow_non_month_end,
            )
        )
        return 0
    except ExecutionRefused as exc:
        logger.warning("One-shot rebalance execution refused: %s", exc)
        return 2
    except Exception as exc:
        logger.error("One-shot rebalance execution failed: %s", exc)
        return 1
    finally:
        has_active_writer = (
            trader is not None
            and trader.journal_writer is not None
            and trader.journal_writer.started
        )
        if has_active_writer:
            trader.journal_writer.stop()
            trader.journal_writer.drain(timeout=dt.timedelta(seconds=2))
