"""The headless orchestrator: every subsystem, one process, one exit code.

The supervisor is where the phases are wired together. Feed in, bars out, strategy
decisions in, journal events out, and a control plane so an operator can say "stop" or
"flatten now" without a restart. It has no trading logic of its own; everything it does
is delegation plus the exit contract.

The exit contract is the part an operator reads first:

* **78** (``EX_CONFIG``) means the host or the configuration is wrong -- a startup gate
  failed, a document is missing, no control token is set. Nothing was traded, and nothing
  was written to the journal, so a rerun after fixing it is safe.
* **70** (``EX_SOFTWARE``) means the journal could not accept an event. The engine is
  losing history, which is indistinguishable from lying about the past, so it stops
  rather than drop.
* **0** means it stopped cleanly: the trigger was seen, the write queue was drained, and
  the listener closed.

The one rule about *when* things happen is that nothing on this path blocks the event
loop. SQLite commits belong to the writer's thread (AGENTS.md 2.2); the loop submits
with ``put_nowait`` and waits for the drain barrier. That is asserted here rather than
assumed, because this is the layer where it would be broken.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

from engine.domain.events import DomainEvent, EventType
from engine.journal.writer import EXIT_JOURNAL_EXHAUSTED as _WRITER_EXIT_JOURNAL
from engine.journal.writer import JournalExhausted, JournalWriter
from engine.market.builder import BarAggregator, BarBuilder
from engine.market.instrument import CURRENT_INSTRUMENT, InstrumentSpec
from engine.market.models import Bar, Quote
from engine.news.breaker import BreakerConfig, SignalBreaker
from engine.news.calendar import NewsCalendar
from engine.risk.config import RiskConfig, RiskConfigError, load_risk_config
from engine.risk.decimal_yaml import DecimalYamlError, load_decimal_yaml
from engine.risk.killswitch import KillSwitch
from engine.risk.sizing import size_position
from engine.runtime.control import ControlServer
from engine.runtime.gates import EXIT_BLOCKED, RuntimeGateError, run_all_gates
from engine.runtime.platform import (
    ControlPlaneError,
    ControlTokenError,
    get_control_endpoint,
    get_control_token,
)
from engine.runtime.signals import DEFAULT_SHUTDOWN_SIGNALS, ShutdownController, SignalRouter
from engine.runtime.systemd import SystemdNotifier
from engine.strategy.backtest import BacktestRunner, ReplayEvent, ReplayEventKind
from engine.strategy.config import (
    StrategyConfig,
    StrategyConfigError,
    load_strategy_config,
    strategy_version_of,
)
from engine.strategy.silver_bullet import (
    CancelOrder,
    FlattenPosition,
    PlaceLimit,
    SilverBulletStrategy,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = [
    "EXIT_BLOCKED",
    "EXIT_JOURNAL_EXHAUSTED",
    "SHUTDOWN_BUDGET",
    "Supervisor",
    "SupervisorHealth",
    "SupervisorSettings",
    "SupervisorSurface",
]

#: Host/config failure. Re-exported from gates so a caller needs one import.
EXIT_BLOCKED = EXIT_BLOCKED

#: Journal exhaustion. Re-exported from the writer that raises it, so the exit code
#: lives beside the condition it maps.
EXIT_JOURNAL_EXHAUSTED: Final[int] = _WRITER_EXIT_JOURNAL

#: The whole graceful shutdown has to finish inside this, whatever it is doing.
SHUTDOWN_BUDGET: Final[dt.timedelta] = dt.timedelta(seconds=10)

#: How long the writer gets to drain before the supervisor gives up on it. Half the
#: budget, so the stop (and the rest of the sequence) still has room.
_DRAIN_SHARE: Final[dt.timedelta] = dt.timedelta(seconds=5)


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Whether the three dependencies the watchdog cares about are healthy."""

    loop_alive: bool
    journal_thread_alive: bool
    queue_healthy: bool
    reasons: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.loop_alive and self.journal_thread_alive and self.queue_healthy

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


class SupervisorHealth:
    """The probe a watchdog ping consults before it is sent.

    Three things, each of which can fail while the process still looks alive:

    * **The event loop.** A beat the supervisor's own task records; if the loop stops
        turning it over, nothing will be pinging anyway, but the check also catches a
        beat task that died while the loop carried on.
    * **The journal worker thread.** No thread, no commits, and no history.
    * **The bounded queue.** A queue at its limit is a worker that cannot keep up, and
        the next submit raises ``JournalExhausted`` -- which is exit 70, not a restart.

    A probe that says unhealthy must not be answered with a ping: systemd would leave a
    dying service alone.
    """

    __slots__ = ("_beat_at", "_beat_timeout", "_clock", "_journal")

    def __init__(
        self,
        *,
        journal: object,
        beat_timeout: dt.timedelta = dt.timedelta(seconds=30),
        clock: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._journal = journal
        self._beat_timeout = beat_timeout
        self._clock = clock if clock is not None else _now
        self._beat_at: dt.datetime | None = None

    def beat(self) -> None:
        """Record that the event loop turned over."""
        self._beat_at = self._clock()

    @property
    def beat_at(self) -> dt.datetime | None:
        return self._beat_at

    def probe(self) -> tuple[bool, str]:
        """Return ``(healthy, reason)`` for the watchdog to decide on."""
        report = self.report()
        return report.healthy, report.reason

    def report(self) -> HealthReport:
        """The full report, so a status command can show which part is wrong."""
        now = self._clock()
        beat_fresh = self._beat_at is not None and (now - self._beat_at) <= self._beat_timeout
        thread: object = getattr(self._journal, "writer_thread_id", None)
        thread_alive = isinstance(thread, int) and thread > 0

        depth = int(getattr(self._journal, "queue_depth", 0))
        limit = int(getattr(self._journal, "queue_maxsize", QUEUE_MAXSIZE))
        queue_ok = depth < limit

        reasons: list[str] = []
        if not beat_fresh:
            reasons.append(f"the event loop has not beaten for {self._beat_timeout}")
        if not thread_alive:
            reasons.append("the journal worker thread is not running")
        if not queue_ok:
            reasons.append(f"the journal queue is at {depth} of {limit}")

        return HealthReport(
            loop_alive=beat_fresh,
            journal_thread_alive=thread_alive,
            queue_healthy=queue_ok,
            reasons=tuple(reasons),
        )


#: The bounded queue's limit, used when the journal does not publish one.
QUEUE_MAXSIZE: Final[int] = 10_000

#: How often to ping when systemd asks for nothing else. systemd always sets
#: `WATCHDOG_USEC`; this is only for a run that has none.
_DEFAULT_WATCHDOG_INTERVAL: Final[dt.timedelta] = dt.timedelta(seconds=15)


# --------------------------------------------------------------------------- #
# the run
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class SupervisorSettings:
    """Everything the supervisor needs to know that is not a subsystem.

    ``journal_path`` and the config paths are resolved by the caller, so a test can
    point them at a temporary directory. ``control_port`` of ``0`` binds a free port;
    the value actually bound is readable from the control server afterwards.
    """

    run_id: str = "xauusd-run"
    journal_path: Path = Path("journal.db")
    strategy_config_path: Path = Path("config") / "strategy.yaml"
    risk_config_path: Path = Path("config") / "risk.yaml"
    breaker_config_path: Path = Path("config") / "breaker.yaml"
    schedule_path: Path = Path("config") / "schedule.yaml"
    control_port: int = 8765
    signal_numbers: tuple[int, ...] = DEFAULT_SHUTDOWN_SIGNALS
    snapshot_every_bars: int = 12
    equity: Decimal = Decimal("100000")
    shutdown_budget: dt.timedelta = SHUTDOWN_BUDGET

    def with_journal(self, path: Path) -> SupervisorSettings:
        fields = self.__dict__.copy()
        fields["journal_path"] = path
        return SupervisorSettings(**fields)


class SupervisorSurface:
    """The control-plane face of a running supervisor.

    The server speaks commands; the supervisor answers them. This adapter exists so
    the server depends on a protocol rather than on the orchestrator, which keeps the
    two independently testable -- and it is safe to read before ``run`` has wired
    anything up, because a question asked of an engine that has not started is still a
    question with an answer.
    """

    __slots__ = ("_owner",)

    def __init__(self, owner: Supervisor) -> None:
        self._owner = owner

    def status(self) -> dict[str, object]:
        return self._owner.status()

    def pause(self) -> str:
        return self._owner.pause()

    def resume(self) -> str:
        return self._owner.resume()

    def kill_flatten(self) -> str:
        return self._owner.kill_flatten()

    def request_shutdown(self) -> str:
        return self._owner.request_shutdown()


class Supervisor:
    """The orchestrator.

    Args:
        settings: Paths, port and run id.
        journal: A started-or-not Phase 2 writer. Injected so a test can use a double.
        quote_source: Anything with ``run(on_quote, *, should_stop=...)``: the OANDA
            stream in production, a scripted list in tests.
        strategy: The Phase 4 strategy. Built by :meth:`build`.
        spec: The instrument geometry, from OANDA in production.
    """

    def __init__(
        self,
        *,
        settings: SupervisorSettings,
        journal: object,
        quote_source: object,
        strategy: SilverBulletStrategy | None = None,
        spec: InstrumentSpec | None = None,
        shutdown: ShutdownController | None = None,
        router: SignalRouter | None = None,
        notifier: SystemdNotifier | None = None,
        sizer: Callable[[Decimal, Decimal], Decimal] | None = None,
        calendar: NewsCalendar | None = None,
    ) -> None:
        self.settings = settings
        self._journal = journal
        self._source = quote_source
        self._spec = spec if spec is not None else _default_spec()
        self._shutdown = shutdown if shutdown is not None else ShutdownController()
        self._router = router or SignalRouter(controller=self._shutdown)
        self._notifier = notifier
        self._sizer = sizer
        self._health = SupervisorHealth(journal=journal)
        self._watchdog_task: asyncio.Task[None] | None = None
        # The news calendar is injected rather than loaded: it needs a transport, and a
        # supervisor that cannot start without the network is a supervisor that cannot
        # be tested. Without one the spread and ATR gates still apply.
        self._calendar = calendar

        self._strategy = strategy
        self._runner: BacktestRunner | None = None
        self._control: ControlServer | None = None
        self._control_surface: SupervisorSurface | None = None
        self._builder: BarBuilder | None = None
        self._aggregator: BarAggregator | None = None
        self._breaker: SignalBreaker | None = None
        self._kill_switch: KillSwitch | None = None
        self._strategy_config: StrategyConfig | None = None
        self._version = None
        self._paused = False
        self._stopping = False
        self._last_quote: Quote | None = None
        self._blocked_reason = ""
        self._equity = Decimal("100000")
        self._journal_open = False

    # -- construction ---------------------------------------------------------- #
    @classmethod
    def build(
        cls,
        *,
        journal: object,
        quote_source: object,
        strategy_config: StrategyConfig,
        settings: SupervisorSettings,
        spec: InstrumentSpec | None = None,
        shutdown: ShutdownController | None = None,
        router: SignalRouter | None = None,
        notifier: SystemdNotifier | None = None,
        sizer: Callable[[Decimal, Decimal], Decimal] | None = None,
        risk: RiskConfig | None = None,
    ) -> Supervisor:
        """Assemble a supervisor with its Phase 2/3/4 collaborators already wired.

        The strategy config is passed in rather than loaded, so the *build* step never
        fails: configuration is a ``run``-time concern with its own exit code, and a
        constructor that raises turns a host problem into a traceback.

        ``risk`` is what the default sizer sizes with. Fixed-fractional sizing belongs to
        Phase 3, and it is handed over here so the strategy layer never reaches into the
        risk package at runtime.
        """
        self = cls(
            settings=settings,
            journal=journal,
            quote_source=quote_source,
            spec=spec,
            shutdown=shutdown,
            router=router,
            notifier=notifier,
            sizer=sizer,
        )
        self._strategy_config = strategy_config
        self._strategy = SilverBulletStrategy(
            config=strategy_config,
            spec=self._spec,
            run_id=settings.run_id,
            quantity_fn=self._quantity_for,
        )
        self._equity = settings.equity
        self._risk = risk
        self._runner = BacktestRunner(
            strategy=self._strategy,
            spec=self._spec,
            run_id=settings.run_id,
            observer=self._observe,
        )
        return self

    @classmethod
    def build_for(
        cls,
        *,
        quote_source: object,
        settings: SupervisorSettings,
        journal: object | None = None,
        notifier: SystemdNotifier | None = None,
    ) -> Supervisor:
        """Assemble a real supervisor: a journal writer, the strategy, and everything else.

        The one real collaboration this builds is the journal writer, because that is
        the subsystem with a lifecycle (a thread and a connection) that the supervisor
        must own. Everything else is configuration read at ``run`` time, where a failure
        has the exit code it deserves.
        """
        resolved_journal = journal if journal is not None else JournalWriter(
            path=settings.journal_path,
            instrument=_default_spec(),
            clock=_monotonic_seconds,
        )
        strategy_config = load_strategy_config(settings.strategy_config_path)
        risk = load_risk_config(settings.risk_config_path)
        return cls.build(
            journal=resolved_journal,
            quote_source=quote_source,
            strategy_config=strategy_config,
            risk=risk,
            settings=settings,
            notifier=notifier,
        )

    def _quantity_for(self, entry: Decimal, stop: Decimal) -> Decimal:
        """Size a position from the injected sizer, or hold one unit.

        Risk sizing is injected rather than imported so ``strategy/`` never reaches into
        ``risk/`` at runtime: the two meet here, at the wiring, where a dependency would
        be legitimate anyway. Without a sizer the strategy holds the venue minimum.
        """
        if self._sizer is not None:
            return self._sizer(entry, stop)
        if self._risk is None:
            return Decimal(1)
        sized = size_position(
            equity=self._equity,
            stop_distance=abs(entry - stop),
            risk_per_trade=self._risk.risk_per_trade,
            spec=self._spec,
        )
        return sized.units

    # -- introspection --------------------------------------------------------- #
    @property
    def surface(self) -> SupervisorSurface:
        """The control-plane face, safe to read before ``run`` starts."""
        if self._control_surface is None:
            self._control_surface = SupervisorSurface(self)
        return self._control_surface

    @property
    def shutdown(self) -> ShutdownController:
        """The shutdown latch a signal or a control command sets."""
        return self._shutdown

    @property
    def runner(self) -> BacktestRunner:
        """The replay engine driving the strategy, once ``run`` has wired it."""
        if self._runner is None:
            raise RuntimeError("the supervisor has not been built")
        return self._runner

    @property
    def strategy(self) -> SilverBulletStrategy:
        if self._strategy is None:
            raise RuntimeError("the supervisor has not been built")
        return self._strategy

    def status(self) -> dict[str, object]:
        """The state a STATUS command reports."""
        runner = self._runner
        open_positions = len(self.strategy.positions) if self._strategy is not None else 0
        resting = len(self.strategy.resting_orders) if self._strategy is not None else 0
        state = "PAUSED" if self._paused else "RUNNING"
        if self._shutdown.requested:
            state = "STOPPING"
        return {
            "state": state,
            "run_id": self.settings.run_id,
            "instrument": self._spec.name,
            "bars": runner.bars_consumed if runner is not None else 0,
            "open_positions": open_positions,
            "resting_orders": resting,
            "blocked": self._blocked_reason,
        }

    # -- the control-plane commands -------------------------------------------- #
    def pause(self) -> str:
        """Stop opening new positions without stopping anything else."""
        self._paused = True
        self._submit_now(EventType.FEED_STATE_CHANGED, {"state": "PAUSED"})
        return "paused; exits still work"

    def resume(self) -> str:
        self._paused = False
        self._submit_now(EventType.FEED_STATE_CHANGED, {"state": "RUNNING"})
        return "resumed"

    def kill_flatten(self) -> str:
        """Withdraw every resting order and close every position at market."""
        runner = self._runner
        if runner is None:
            return "nothing to flatten: the engine has not started"

        bar = self._current_bar()
        trades = runner.flatten_all(bar=bar, reason="control plane KILL_FLATTEN")
        withdrawn = runner.withdraw_all(
            at=self._now(), reason="control plane KILL_FLATTEN"
        )
        for trade in trades:
            self._submit_now(
                EventType.POSITION_CLOSED,
                {
                    "position_id": trade.trade_id,
                    "amount": trade.pnl,
                    "price": trade.exit_price,
                    "r_multiple": trade.r_multiple,
                    "reason": trade.exit_reason.value,
                },
                at=trade.exit_time,
            )
        return f"flattened {len(trades)} positions and withdrew {len(withdrawn)} orders"

    def request_shutdown(self) -> str:
        """Ask for a graceful stop; the run loop notices on its next bar."""
        self._shutdown.request_shutdown("control plane")
        return "shutting down"

    # -- the run ---------------------------------------------------------------- #
    async def run(self) -> int:
        """Run the engine until it stops, and return the process exit code.

        The two failure modes are deliberately nested rather than parallel: a *blocked*
        start is 78 and nothing has been written, while an exhausted journal is 70 and
        the engine has history it cannot finish. Both end with the same graceful stop,
        because a journal that cannot accept an event still has events in flight.
        """
        try:
            try:
                await self._begin()
            except _BLOCKING_ERRORS as exc:
                logger.error("[engine] BLOCKED: %s", exc)
                return EXIT_BLOCKED
            await self._run_feed()
        except JournalExhausted as exc:
            logger.error("[engine] journal exhausted: %s", exc)
            return EXIT_JOURNAL_EXHAUSTED
        finally:
            await self._end()
        return 0

    async def _run_watchdog(self) -> None:
        """Beat the loop, and ping only while everything is healthy.

        Two jobs in one task on purpose. The beat is what tells systemd the event loop
        is turning over, and the ping is withheld unless the journal worker and its queue
        are healthy too -- so a wedged worker is reported by the ping's *absence*, which
        is the one signal systemd acts on.
        """
        if self._notifier is None:
            return
        interval = self._notifier.watchdog_interval
        if interval <= dt.timedelta(0):
            interval = _DEFAULT_WATCHDOG_INTERVAL
        self._health.beat()
        while not self._shutdown.requested:
            await asyncio.sleep(interval)
            self._health.beat()
            if not self._notifier.watchdog():
                logger.warning(
                    "[engine] watchdog ping withheld: %s", self._notifier.last_decline
                )

    async def _begin(self) -> None:
        """Everything that can refuse to start does so before the market does."""
        run_all_gates()

        self._strategy_config = load_strategy_config(self.settings.strategy_config_path)
        document = load_decimal_yaml(self.settings.strategy_config_path)
        self._version = strategy_version_of(
            document, config_path=str(self.settings.strategy_config_path)
        )
        risk = load_risk_config(self.settings.risk_config_path)
        self._kill_switch = _kill_switch_for(risk)
        self._breaker = SignalBreaker(
            spec=self._spec, config=_breaker_config(self.settings.breaker_config_path)
        )
        get_control_token()

        self._builder = BarBuilder(spec=self._spec)
        self._aggregator = BarAggregator(spec=self._spec)
        self._journal.start()
        self._journal_open = True

        self._submit_now(
            EventType.RUN_STARTED,
            {
                "run_id": self.settings.run_id,
                **self._version.header_payload(),
            },
        )
        self._control = ControlServer(
            surface=self.surface,
            token=get_control_token(),
            host=get_control_endpoint()[0],
            port=self.settings.control_port,
        )
        await self._control.start()
        self._router.install(asyncio.get_running_loop())
        if self._notifier is not None:
            self._notifier.ready()
            self._notifier.status("running")
            self._watchdog_task = asyncio.create_task(
                self._run_watchdog(), name="xauusd-watchdog"
            )

    async def _run_feed(self) -> None:
        """Consume the stream until it ends, fails, or a shutdown is requested."""
        stop = self._shutdown
        try:
            await self._source.run(
                self._on_quote,
                should_stop=lambda: stop.requested or self._stopping,
            )
        except JournalExhausted:
            # A journal that cannot accept an event is the engine's failure, not the
            # feed's. Re-raised so ``run`` maps it to 70.
            raise
        except Exception as exc:
            # The stream going away is a systemd problem, not an engine defect: the
            # engine stops, drains, and exits 0 having recorded what happened.
            logger.error("[engine] feed failed: %s", exc)
            self._shutdown.request_shutdown("the feed failed")
            self._submit_now(
                EventType.FEED_STATE_CHANGED,
                {"state": "FAILED", "reason": str(exc)},
            )
            return

        if not stop.requested:
            self._shutdown.request_shutdown("the feed ended")
            self._submit_now(
                EventType.FEED_STATE_CHANGED,
                {"state": "STOPPED", "reason": "the feed ended"},
            )

    def _on_quote(self, quote: Quote) -> None:
        """Observe one quote, then fold it into the bars the strategy reads.

        Deliberately synchronous: the quote sink is called by the stream, on its task,
        and a coroutine handed to it would be created and dropped on the floor without
        ever being awaited.
        """
        self._last_quote = quote
        if self._breaker is not None:
            self._breaker.observe_quote(quote)
        if self._builder is None or self._aggregator is None:
            return
        for minute in self._builder.add_quote(quote):
            self._on_minute_bar(minute)

    def _on_minute_bar(self, minute: Bar) -> None:
        """Aggregate one minute bar, and hand every completed M5 bar to the strategy."""
        if self._aggregator is None:
            return
        for bar in self._aggregator.add(minute):
            self._on_bar(bar)

    def _on_bar(self, bar: Bar) -> None:
        """Settle the strategy's book on one closed bar, then let it look for a setup.

        A blocked engine still settles exits. Blocking an exit is how a stop becomes a
        blow-up, the same rule the Phase 3 kill-switch already enforces on its side.
        """
        self._refresh_block(bar)
        trades = self.runner.advance(bar, entries=not self._blocked_reason)
        for trade in trades:
            self._record_trade(trade)
        if self.runner.bars_consumed % self.settings.snapshot_every_bars == 0:
            self._snapshot_equity()

    def _refresh_block(self, bar: Bar) -> None:
        """Decide whether new entries are allowed, and remember why not."""
        if self._paused:
            self._blocked_reason = "paused"
            return
        if self._kill_switch is not None and self._kill_switch.is_tripped:
            self._blocked_reason = self._kill_switch.trip_reason or "kill-switch tripped"
            return
        if self._breaker is not None:
            blackouts = ()
            if self._calendar is not None:
                blackouts = self._calendar.blackouts_on(bar.timestamp_utc)
            decision = self._breaker.evaluate(bar.timestamp_utc, blackouts)
            if decision.blocked:
                self._blocked_reason = ",".join(reason.value for reason in decision.reasons)
                return
        self._blocked_reason = ""

    def _record_trade(self, trade: object) -> None:
        """Turn one closed trade into the events a reader needs to rebuild it."""
        self._submit_now(
            EventType.POSITION_CLOSED,
            {
                "position_id": trade.trade_id,
                "side": trade.side.value,
                "quantity": str(trade.quantity),
                "amount": trade.pnl,
                "price": trade.exit_price,
                "cost": trade.entry_price,
                "r_multiple": trade.r_multiple,
                "reason": trade.exit_reason.value,
                "reference": trade.reference,
            },
            at=trade.exit_time,
        )
        if self._kill_switch is not None:
            # Recorded in R, which is the currency-independent form: the kill-switch's
            # limit is denominated in R, and a currency amount would need the equity the
            # trade was sized against rather than the trade itself.
            self._kill_switch.record_realized_r(trade.r_multiple, trade.exit_time)

    def _snapshot_equity(self) -> None:
        """Record an equity mark, which is what the projection's curve is built from."""
        self._submit_now(
            EventType.EQUITY_SNAPSHOT,
            {
                "equity": str(self._equity),
                "run_id": self.settings.run_id,
                "bars": self.runner.bars_consumed,
            },
        )

    # -- journaling ------------------------------------------------------------- #
    def _submit_now(
        self,
        event_type: EventType,
        payload: dict[str, object],
        *,
        at: dt.datetime | None = None,
    ) -> None:
        """Submit one event, or raise ``JournalExhausted`` up to ``run``.

        The event loop only ever calls ``put_nowait``; the writer's thread does the SQL
        (AGENTS.md 2.2). A full queue is exactly the condition the supervisor is
        supposed to exit 70 for, so it is raised rather than caught here.
        """
        if not self._journal_open:
            return
        moment = at if at is not None else _now()
        self._journal.submit(
            DomainEvent(
                event_id=_event_id(event_type.value),
                event_type=event_type,
                occurred_at=moment,
                payload=payload,
            )
        )

    def _observe(self, event: ReplayEvent) -> None:
        """Journal what the replay engine just did.

        The strategy layer has no idea what a journal is. It emits events, and this is
        the wiring that turns them into rows: an intent becomes an ORDER_* event, a fill
        becomes FILL_REPORTED plus SLIPPAGE_REALIZED, and nothing is invented.
        """
        if event.kind is ReplayEventKind.INTENT and isinstance(event.intent, PlaceLimit):
            self._submit_now(
                EventType.ORDER_SUBMITTED,
                {
                    "order_id": event.intent.order_id,
                    "side": event.intent.side.value,
                    "kind": "LIMIT",
                    "quantity": str(event.intent.quantity),
                    "level": event.intent.limit_price,
                    "stop": str(event.intent.stop_price),
                    "target": str(event.intent.target_price),
                    "reference": event.intent.reference.value,
                },
                at=event.intent.placed_at,
            )
        if event.kind is ReplayEventKind.INTENT and isinstance(event.intent, CancelOrder):
            self._submit_now(
                EventType.ORDER_CANCELLED,
                {
                    "order_id": event.intent.order_id,
                    "reason": event.intent.reason,
                },
                at=event.intent.at,
            )
        if event.kind is ReplayEventKind.INTENT and isinstance(event.intent, FlattenPosition):
            self._submit_now(
                EventType.ORDER_CANCELLED,
                {
                    "order_id": event.intent.position_id,
                    "reason": event.intent.reason,
                },
                at=event.intent.at,
            )
        if event.kind is ReplayEventKind.ENTRY_FILL and event.fill is not None:
            self._submit_now(
                EventType.FILL_REPORTED,
                {
                    "order_id": event.fill.order_id,
                    "side": event.fill.side.value,
                    "quantity": str(event.fill.quantity),
                    "price": event.fill.price,
                    "level": event.fill.reference_price,
                    "kind": event.fill.kind.value,
                    "slippage_ticks": event.fill.slippage_ticks,
                    "seed": event.fill.seed,
                },
                at=event.fill.filled_at,
            )
            self._submit_now(
                EventType.SLIPPAGE_REALIZED,
                {
                    "order_id": event.fill.order_id,
                    "amount": event.fill.slippage,
                    "price": event.fill.price,
                },
                at=event.fill.filled_at,
            )
            self._submit_now(
                EventType.POSITION_OPENED,
                {
                    "position_id": event.fill.order_id,
                    "side": event.fill.side.value,
                    "quantity": str(event.fill.quantity),
                    "cost": event.fill.price,
                    "price": event.fill.price,
                },
                at=event.fill.filled_at,
            )

    # -- helpers ---------------------------------------------------------------- #
    def _current_bar(self) -> Bar:
        """A bar standing in for "right now", for a forced exit.

        The last quote is the market. A flat bar around its mid is the honest price to
        flatten against; inventing a range would invent a result.
        """
        quote = self._last_quote
        if quote is None:
            raise RuntimeError("no quote has arrived, so there is no book to flatten")
        from engine.market.models import OHLC

        return Bar(
            timeframe="M5",
            timestamp_utc=quote.timestamp_utc,
            complete=True,
            volume=0,
            bid_ohlc=OHLC(open=quote.bid, high=quote.bid, low=quote.bid, close=quote.bid),
            ask_ohlc=OHLC(open=quote.ask, high=quote.ask, low=quote.ask, close=quote.ask),
        )

    def _now(self) -> dt.datetime:
        return _now()

    async def _end(self) -> None:
        """The graceful stop, in the only order that is safe.

        Stop hearing, stop listening, drain what was heard, then hand back the exit code.
        The budget is honoured in wall-clock time rather than hoped for, because a
        supervisor that overruns it has already lost the race systemd started.
        """
        budget = self.settings.shutdown_budget
        started = time.monotonic()
        self._stopping = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
        self._router.uninstall(asyncio.get_running_loop())
        if self._control is not None:
            await self._control.stop()
        if self._notifier is not None:
            self._notifier.status("draining")
        if self._journal_open:
            drained = await self._drain_journal(budget, started)
            remaining = _remaining(budget, started)
            if remaining <= dt.timedelta(0):
                # The drain spent the whole budget. systemd's watchdog will kill the
                # process, which is the correct outcome for a journal that cannot stop.
                logger.error("[engine] no budget left for the journal worker to stop")
            else:
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self._journal.stop, remaining),
                        timeout=remaining.total_seconds(),
                    )
                except TimeoutError:
                    logger.error("[engine] the journal worker did not stop inside the budget")
            self._journal_open = False
            if not drained:
                logger.warning("[engine] journal did not drain inside the budget")
        if self._notifier is not None:
            self._notifier.status("stopped")

    async def _drain_journal(self, budget: dt.timedelta, started: float) -> bool:
        """Wait for the queue to empty, inside its share of the budget.

        ``JournalWriter.drain`` and ``stop`` are shutdown barriers and block by design,
        so both run off the loop. A barrier that blocks the loop makes the shutdown
        deadline unenforceable, which is the whole point of the budget.
        """
        share = min(_DRAIN_SHARE, _remaining(budget, started))
        try:
            return bool(
                await asyncio.wait_for(
                    asyncio.to_thread(self._journal.drain, share),
                    timeout=_seconds_left(budget, started),
                )
            )
        except TimeoutError:
            return False


def _now() -> dt.datetime:
    """The current instant, in the only form the journal stores."""
    return dt.datetime.now(dt.UTC)


def _seconds_left(budget: dt.timedelta, started: float) -> float:
    """Seconds of budget left, as a float a barrier API can wait for."""
    return max((budget - _elapsed(started)).total_seconds(), 0.0)


def _elapsed(started: float) -> dt.timedelta:
    return dt.timedelta(seconds=max(time.monotonic() - started, 0.0))


def _remaining(budget: dt.timedelta, started: float) -> dt.timedelta:
    """Whatever is left of the shutdown budget, never less than nothing."""
    left = budget - _elapsed(started)
    return left if left > dt.timedelta(0) else dt.timedelta(0)





def _default_spec() -> InstrumentSpec:
    """The committed XAU_USD geometry, used whenever the API is not reachable.

    The fixture is the fallback Phase 1 already carries, so a supervisor that runs
    offline does not need a transport to know what a tick is. The loader that would
    consult the API belongs to the phase that runs the real feed.
    """
    spec_path = (
        Path(__file__).resolve().parents[3]
        / "engine"
        / "tests"
        / "fixtures"
        / "instruments"
        / "XAU_USD.yaml"
    )
    if not spec_path.is_file():
        return InstrumentSpec(
            name=CURRENT_INSTRUMENT,
            pip_location=-4,
            display_precision=2,
            trade_units_precision=5,
            minimum_trade_size=Decimal("1"),
        )
    import yaml

    return InstrumentSpec.from_oanda_payload(yaml.safe_load(spec_path.read_text("utf-8")))


def _breaker_config(path: Path) -> BreakerConfig:
    return BreakerConfig.from_mapping(load_decimal_yaml(path))


def _kill_switch_for(risk: RiskConfig) -> KillSwitch:
    return KillSwitch(config=risk)


def _monotonic_seconds() -> float:
    """The journal writer's clock, which measures group-commit intervals."""
    return time.monotonic()


def _require_source() -> object:
    """The entry point's refusal when no feed has been wired.

    A supervisor without a quote stream would start, build bars out of nothing, and
    report a healthy day. Refusing loudly is the honest instruction.
    """
    raise SystemExit(
        "xauusd-engine: no quote source was injected; the live OANDA feed is wired by "
        "the phase that runs the engine, not by this module"
    )


def _event_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def main(
    *,
    quote_source: object,
    settings: SupervisorSettings | None = None,
    journal: object | None = None,
) -> int:
    """Run the engine as a process, and return the exit code.

    The quote source is a parameter rather than an import because the live OANDA wiring
    belongs to the phase that runs a live engine: a supervisor that cannot be built
    without a broker connection cannot be tested, and one that silently invents a
    transport is worse than one that refuses to run.

    Args:
        quote_source: Anything with ``run(on_quote, *, should_stop=...)``.
        settings: Paths, port and run id; defaults are the repository's own.

    Returns:
        ``EXIT_BLOCKED`` for a host or configuration problem, ``EXIT_JOURNAL_EXHAUSTED``
        for a journal that could not accept an event, ``0`` otherwise.
    """
    logging.basicConfig(
        level=os.environ.get("XAUUSD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    resolved = settings if settings is not None else SupervisorSettings()
    supervisor = Supervisor.build_for(
        quote_source=quote_source,
        settings=resolved,
        journal=journal,
    )
    return asyncio.run(supervisor.run())


if __name__ == "__main__":
    raise SystemExit(main(quote_source=_require_source()))


#: Everything that means "this host cannot run the engine", which is exit 78 rather
#: than a crash. Deliberately does not include a journal failure: that is 70, because
#: it is the engine losing history rather than the host being wrong, and a caller that
#: cannot tell those apart will restart into the same loss.
_BLOCKING_ERRORS = (
    RuntimeGateError,
    StrategyConfigError,
    DecimalYamlError,
    RiskConfigError,
    ControlPlaneError,
    ControlTokenError,
)
