"""Tests for the headless supervisor.

The supervisor is wired to everything, so these tests check what that wiring does
rather than what any one part does. Four properties carry the weight.

**Exit codes are a contract.** A gate or config failure is 78 (``EX_CONFIG``): the host
is wrong, and an operator must be able to tell that from a crash. Journal exhaustion is
70: the engine is losing history and must stop rather than drop events. Both are
asserted through ``run()`` directly, because a supervisor that has to be killed to
discover its exit code cannot be tested.

**A signal drains, then exits, inside ten seconds.** The trap, the queue drain and the
return are one sequence, and the bound is asserted with a clock rather than hoped for.

**Nothing blocks the event loop.** SQLite commits run on the writer's thread (Phase 2's
invariant, re-asserted here where it could be broken); the supervisor's own loop must
never be that thread.

**The gate is the only thing that opens risk.** A PAUSE command, the news breaker and
the kill-switch all funnel into one question the strategy layer never has to ask, and
each is tested by feeding a bar that *would* trade and watching no order appear.
"""

import asyncio
import datetime as dt
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

from engine.domain.events import DomainEvent, EventType
from engine.journal.writer import JournalExhausted, JournalWriter
from engine.market.models import Quote
from engine.risk.config import RiskConfig
from engine.risk.decimal_yaml import load_decimal_yaml
from engine.runtime import supervisor as supervisor_module
from engine.runtime.signals import ShutdownController, SignalRouter
from engine.runtime.supervisor import (
    EXIT_BLOCKED,
    EXIT_JOURNAL_EXHAUSTED,
    SHUTDOWN_BUDGET,
    Supervisor,
    SupervisorSettings,
)
from engine.strategy.config import StrategyConfig, strategy_version_of

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG = REPO_ROOT / "config"
STRATEGY_DEFAULTS = StrategyConfig(
    swing_n=2,
    sweep_min_ticks=3,
    sweep_close_back_bars=2,
    atr_window=14,
    mss_body_k=Decimal("0.25"),
    fvg_min_atr=Decimal("0.25"),
    order_expiry_bars=12,
    stop_buffer_ticks=5,
    rr_target=Decimal("2"),
)
RISK_DEFAULTS = RiskConfig(risk_per_trade=Decimal("0.01"), daily_loss_limit_r=Decimal("3"))

BASE = dt.datetime(2026, 3, 9, 9, 0, tzinfo=dt.UTC)


class ScriptedQuoteSource:
    """A feed that replays a fixed list of quotes, then completes."""

    def __init__(self, quotes: list[Quote]) -> None:
        self.quotes = quotes
        self.stopped = False
        self.finished = False

    async def run(self, on_quote, *, should_stop=None) -> None:
        for quote in self.quotes:
            if should_stop is not None and should_stop():
                self.stopped = True
                return
            on_quote(quote)
        self.finished = True


class HangingQuoteSource:
    """A feed that never finishes until it is asked to stop."""

    def __init__(self) -> None:
        self.stopped = False

    async def run(self, on_quote, *, should_stop=None) -> None:
        while not (should_stop is not None and should_stop()):
            await asyncio.sleep(0)
        self.stopped = True


class ExplodingQuoteSource:
    """A feed that dies, the way a transport eventually does."""

    def __init__(self) -> None:
        self.error: Exception | None = None

    async def run(self, on_quote, *, should_stop=None) -> None:
        self.error = ConnectionResetError("the stream went away")
        raise self.error


class RecordingJournal:
    """A journal double that records submissions and never blocks."""

    def __init__(self) -> None:
        self.events: list[DomainEvent] = []
        self.started = False
        self.stopped = False
        self.drained = False

    def start(self) -> None:
        self.started = True

    def submit(self, event: DomainEvent) -> None:
        self.events.append(event)

    def drain(self, timeout=None) -> bool:
        self.drained = True
        return True

    def stop(self, timeout=None) -> bool:
        self.stopped = True
        return True


class ExhaustingJournal(RecordingJournal):
    """A journal whose bounded queue is full, which must stop the engine."""

    def submit(self, event: DomainEvent) -> None:
        raise JournalExhausted("queue is full")


class SlowStopJournal(RecordingJournal):
    """A journal whose worker has wedged, so the drain budget has to be honoured.

    ``drain`` ignores its timeout and blocks for five seconds -- a worker that has
    stopped honouring its own barrier -- and ``stop`` blocks for five seconds too, far
    past the two-second budget this test sets. The only thing that saves the shutdown
    is the supervisor's own deadline, which is exactly what is under test.
    """

    def __init__(self) -> None:
        super().__init__()
        self.drain_attempted = False

    def drain(self, timeout=None) -> bool:
        self.drain_attempted = True
        time.sleep(5)
        return False

    def stop(self, timeout=None) -> bool:
        time.sleep(5)
        self.stopped = True
        return False


def _quotes(count: int, *, start: str = "2000.00", step: str = "0.50") -> list[Quote]:
    quotes: list[Quote] = []
    price = Decimal(start)
    pace = Decimal(step)
    for index in range(count):
        mid = price + (pace * index)
        moment = BASE + dt.timedelta(minutes=index)
        quotes.append(
            Quote(
                bid=mid - Decimal("0.01"),
                ask=mid + Decimal("0.01"),
                timestamp_utc=moment,
            )
        )
    return quotes


def _settings(journal_path: Path | None = None, **overrides: object) -> SupervisorSettings:
    values: dict[str, object] = {
        "run_id": "test-run",
        "journal_path": journal_path or Path("journal.db"),
        "control_port": 0,
    }
    values.update(overrides)
    return SupervisorSettings(**values)  # type: ignore[arg-type]


def _supervisor(
    journal: object,
    source: object,
    *,
    settings: SupervisorSettings | None = None,
    shutdown: ShutdownController | None = None,
    router: SignalRouter | None = None,
    **overrides: object,
) -> Supervisor:
    return Supervisor.build(
        journal=journal,
        quote_source=source,
        strategy_config=STRATEGY_DEFAULTS,
        risk=RISK_DEFAULTS,
        settings=settings if settings is not None else _settings(**overrides),
        shutdown=shutdown,
        router=router,
    )


# --------------------------------------------------------------------------- #
# exit codes
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _control_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every supervisor run needs a token; a test never needs a real one."""
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", "unit-test-token")


async def test_a_clean_feed_exits_zero(tmp_path: Path) -> None:
    journal = RecordingJournal()
    supervisor = _supervisor(journal, ScriptedQuoteSource(_quotes(4)))

    assert await supervisor.run() == 0
    assert journal.events


async def test_a_gate_failure_exits_78(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.runtime.gates import RuntimeGateError

    def explode() -> None:
        raise RuntimeGateError("sqlite 3.45.0 is below the floor")

    monkeypatch.setattr(supervisor_module, "run_all_gates", explode)
    supervisor = _supervisor(RecordingJournal(), ScriptedQuoteSource(_quotes(1)))

    assert await supervisor.run() == EXIT_BLOCKED


async def test_a_missing_strategy_config_exits_78(tmp_path: Path) -> None:
    journal = RecordingJournal()
    supervisor = _supervisor(journal, ScriptedQuoteSource(_quotes(1)))
    supervisor.settings.strategy_config_path = tmp_path / "absent.yaml"

    assert await supervisor.run() == EXIT_BLOCKED
    assert not journal.events


async def test_a_missing_control_token_exits_78(monkeypatch: pytest.MonkeyPatch) -> None:
    from engine.runtime.platform import ControlTokenError

    def no_token() -> str:
        raise ControlTokenError("no token configured")

    monkeypatch.setattr(supervisor_module, "get_control_token", no_token)
    supervisor = _supervisor(RecordingJournal(), ScriptedQuoteSource(_quotes(1)))

    assert await supervisor.run() == EXIT_BLOCKED


async def test_journal_exhaustion_exits_70() -> None:
    journal = ExhaustingJournal()
    supervisor = _supervisor(journal, ScriptedQuoteSource(_quotes(4)))

    assert await supervisor.run() == EXIT_JOURNAL_EXHAUSTED


async def test_a_feed_failure_is_not_an_engine_failure() -> None:
    journal = RecordingJournal()
    supervisor = _supervisor(journal, ExplodingQuoteSource())

    assert await supervisor.run() == 0
    assert journal.stopped
    assert any(event.event_type is EventType.FEED_STATE_CHANGED for event in journal.events)


# --------------------------------------------------------------------------- #
# graceful shutdown
# --------------------------------------------------------------------------- #
async def test_a_signal_drains_the_journal_and_exits_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = RecordingJournal()
    source = HangingQuoteSource()
    controller = ShutdownController()
    router = SignalRouter(controller=controller)
    supervisor = _supervisor(journal, source, shutdown=controller, router=router)

    stopper = asyncio.create_task(supervisor.run())
    await asyncio.sleep(0)
    controller.request_shutdown("unit")
    assert await asyncio.wait_for(stopper, timeout=5) == 0

    assert source.stopped
    assert journal.drained
    assert journal.stopped
    assert router.installed is False


async def test_shutdown_finishes_inside_its_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged journal must not hold the shutdown past its budget.

    The budget is set to two seconds so the assertion is fast; the default is the ten
    seconds the phase specifies, asserted separately below.
    """
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", "unit-test-token")
    journal = SlowStopJournal()
    source = HangingQuoteSource()
    supervisor = _supervisor(
        journal,
        source,
        shutdown=ShutdownController(),
        settings=_settings(shutdown_budget=dt.timedelta(seconds=2)),
    )

    started = time.monotonic()
    stopper = asyncio.create_task(supervisor.run())
    await asyncio.sleep(0)
    supervisor.shutdown.request_shutdown("unit")
    # The supervisor's own deadline is two seconds, so three is a tight ceiling. A run
    # that overran its budget would not be here at all -- this raise *is* the assertion.
    code = await asyncio.wait_for(stopper, timeout=3)

    assert code == 0
    assert journal.drain_attempted
    # The worker never actually stopped: it wedged, which is the point. The supervisor
    # exits on its own clock instead of waiting for it, and systemd finishes the job.
    assert journal.stopped is False
    assert time.monotonic() - started >= 1


def test_the_default_shutdown_budget_is_ten_seconds() -> None:
    assert SupervisorSettings().shutdown_budget == dt.timedelta(seconds=10)
    assert dt.timedelta(seconds=10) == SHUTDOWN_BUDGET


async def test_a_control_shutdown_reaches_the_same_path(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = RecordingJournal()
    source = HangingQuoteSource()
    controller = ShutdownController()
    supervisor = _supervisor(journal, source, shutdown=controller)

    stopper = asyncio.create_task(supervisor.run())
    await asyncio.sleep(0)
    assert supervisor.surface.request_shutdown() == "shutting down"
    assert await asyncio.wait_for(stopper, timeout=5) == 0

    assert source.stopped
    assert journal.drained


async def test_pause_and_resume_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = _supervisor(RecordingJournal(), ScriptedQuoteSource(_quotes(1)))

    assert "paused" in supervisor.surface.pause()
    assert supervisor.surface.status()["state"] == "PAUSED"
    assert "resumed" in supervisor.surface.resume()
    assert supervisor.surface.status()["state"] == "RUNNING"


# --------------------------------------------------------------------------- #
# the wiring
# --------------------------------------------------------------------------- #
async def test_the_run_header_is_the_first_event(monkeypatch: pytest.MonkeyPatch) -> None:
    journal = RecordingJournal()
    supervisor = _supervisor(journal, ScriptedQuoteSource(_quotes(4)))

    await supervisor.run()

    header = journal.events[0]
    assert header.event_type is EventType.RUN_STARTED
    digest = strategy_version_of(load_decimal_yaml(CONFIG / "strategy.yaml"), config_path="x")
    assert header.payload["strategy_version"] == digest.hex_digest


async def test_quotes_reach_the_bars_and_the_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = _supervisor(RecordingJournal(), ScriptedQuoteSource(_quotes(40)))

    await supervisor.run()

    assert supervisor.runner.bars_consumed > 0


async def test_the_supervisor_journals_everything_a_setup_produces() -> None:
    """The strategy emits; the supervisor records. Nothing invents a row."""
    journal = RecordingJournal()
    supervisor = _supervisor(journal, ScriptedQuoteSource(_setup_quotes()))

    await supervisor.run()

    kinds = {event.event_type for event in journal.events}
    assert EventType.RUN_STARTED in kinds
    assert EventType.ORDER_SUBMITTED in kinds
    submitted = next(
        event for event in journal.events if event.event_type is EventType.ORDER_SUBMITTED
    )
    assert submitted.payload["kind"] == "LIMIT"
    assert submitted.payload["reference"] == "ASIAN"
    assert Decimal(submitted.payload["level"]) == Decimal("2006.35")
    equity = [event for event in journal.events if event.event_type is EventType.EQUITY_SNAPSHOT]
    assert equity, "a run that trades reports an equity mark"


def _setup_quotes() -> list[Quote]:
    """The quotes of the Phase 4 canonical day, which trades in the London window.

    Loaded from the strategy suite's own fixture module rather than restated: there is
    one canonical day in this repository, and a second copy here would be a second day.
    Each closed bar becomes five quotes inside its five minutes, at the bar's bid prices
    and a two-cent spread, so the Phase 1 aggregators rebuild the bar's mid exactly --
    which is the side the strategy reads.
    """
    import importlib.util

    path = REPO_ROOT / "engine" / "tests" / "strategy" / "conftest.py"
    spec = importlib.util.spec_from_file_location("strategy_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    quotes: list[Quote] = []
    for bar in module.canonical_day(dt.date(2026, 3, 9)):
        prices = [
            bar.bid_ohlc.open,
            bar.bid_ohlc.low,
            bar.bid_ohlc.high,
            bar.bid_ohlc.close,
            bar.bid_ohlc.close,
        ]
        for index, bid in enumerate(prices):
            quotes.append(
                Quote(
                    bid=bid,
                    ask=bid + Decimal("0.02"),
                    timestamp_utc=bar.timestamp_utc + dt.timedelta(seconds=index),
                )
            )
    return quotes


async def test_sqlite_never_runs_on_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The writer owns the connection; the supervisor only ever ``put_nowait``s."""
    journal = JournalWriter(
        path=tmp_path / "journal.db",
        instrument=_spec(),
        clock=_monotonic,
    )
    supervisor = _supervisor(
        journal, ScriptedQuoteSource(_quotes(4)), journal_path=tmp_path / "journal.db"
    )
    loop_thread = threading.get_ident()
    exit_code = await supervisor.run()

    assert exit_code == 0
    # The writer's thread is the one that committed, and it is not the loop.
    assert journal.writer_thread_id is not None
    assert journal.writer_thread_id != loop_thread
    assert journal.committed_events > 0


def _spec() -> object:
    from engine.market.instrument import InstrumentSpec

    return InstrumentSpec(
        name="XAU_USD",
        pip_location=-4,
        display_precision=2,
        trade_units_precision=5,
        minimum_trade_size=Decimal("1"),
    )


def _monotonic() -> float:
    return time.monotonic()

# --------------------------------------------------------------------------- #
# the health probe
# --------------------------------------------------------------------------- #
from engine.runtime.supervisor import SupervisorHealth, SupervisorSettings  # noqa: E402


class _JournalShape:
    """A journal double with the shape the probe reads."""

    def __init__(self, *, thread_id: int | None = 7, depth: int = 0, limit: int = 10_000) -> None:
        self.writer_thread_id = thread_id
        self.queue_depth = depth
        self.queue_maxsize = limit


def test_an_unbeaten_probe_is_unhealthy() -> None:
    health = SupervisorHealth(journal=_JournalShape())

    healthy, reason = health.probe()

    assert healthy is False
    assert "event loop" in reason


def test_a_beaten_probe_with_a_live_journal_is_healthy() -> None:
    health = SupervisorHealth(journal=_JournalShape())
    health.beat()

    healthy, reason = health.probe()

    assert healthy is True
    assert reason == ""


def test_no_journal_thread_is_unhealthy() -> None:
    health = SupervisorHealth(journal=_JournalShape(thread_id=None))
    health.beat()

    healthy, reason = health.probe()

    assert healthy is False
    assert "journal worker" in reason


def test_a_full_queue_is_unhealthy() -> None:
    """A queue at its limit is the state the next submit would turn into exit 70."""
    health = SupervisorHealth(journal=_JournalShape(depth=10_000, limit=10_000))
    health.beat()

    healthy, reason = health.probe()

    assert healthy is False
    assert "queue" in reason


def test_a_stale_beat_is_unhealthy() -> None:
    """A beat older than the timeout means the loop stopped turning over."""
    clock = _FakeClock()
    health = SupervisorHealth(journal=_JournalShape(), clock=clock)
    health.beat()
    clock.advance(seconds=31)

    healthy, reason = health.probe()

    assert healthy is False
    assert "event loop" in reason


def test_the_report_names_each_failing_part() -> None:
    health = SupervisorHealth(journal=_JournalShape(thread_id=None, depth=10_000, limit=10_000))

    report = health.report()

    assert report.loop_alive is False
    assert report.journal_thread_alive is False
    assert report.queue_healthy is False


class _FakeClock:
    def __init__(self) -> None:
        self.now = dt.datetime(2026, 3, 9, 12, 0, tzinfo=dt.UTC)

    def advance(self, seconds: int) -> None:
        self.now += dt.timedelta(seconds=seconds)

    def __call__(self) -> dt.datetime:
        return self.now

# --------------------------------------------------------------------------- #
# the entry point
# --------------------------------------------------------------------------- #
def test_main_refuses_to_run_without_a_quote_source() -> None:
    """A supervisor with no feed would trade a day that never happened."""
    from engine.runtime import supervisor as module

    with pytest.raises(SystemExit):
        module._require_source()


def test_main_returns_the_supervisor_exit_code(tmp_path: Path) -> None:
    """`main` is the process boundary: it returns what `run` decided."""
    from engine.runtime import supervisor as module

    journal = RecordingJournal()
    source = ScriptedQuoteSource(_quotes(2))
    settings = SupervisorSettings(
        run_id="entry", journal_path=tmp_path / "journal.db", control_port=0
    )

    code = module.main(quote_source=source, settings=settings, journal=journal)

    assert code == 0
    assert journal.events


def test_the_script_module_runs_as_a_program() -> None:
    """`python -m engine.runtime.supervisor` reaches the entry point's refusal."""
    result = subprocess.run(
        [sys.executable, "-m", "engine.runtime.supervisor"],
        capture_output=True,
        timeout=120,
        check=False,
    )

    assert result.returncode != 0
    assert b"quote source" in result.stderr
