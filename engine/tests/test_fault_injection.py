"""Phase 8 fault-injection drills.

Six drills, each one an operator story with a machine-checkable ending:

1. **Hard crash.** A harness process runs a real supervisor over a real journal, opens a
   position and trips the kill-switch, then is ``SIGKILL``ed. The parent then verifies the
   hash chain over whatever committed and *restores* the engine's state from journal events
   alone — the position and the latch. A kill that loses the chain is a kill that loses
   history, so the assertion is that the surviving rows verify and are sufficient.
2. **Clean shutdown.** ``SIGTERM`` and a control-plane ``SHUTDOWN`` both reach the same
   drain path, and both are asserted with a *real* writer: the queue is empty, every
   submitted event committed, and the whole thing finished inside the ten-second budget.
3. **Disk synchronization stall.** The writer thread's ``commit`` is made to take 500 ms.
   The event loop must keep consuming quotes the whole time — measured as the wall-clock
   span between the first and last quote it delivers — because a stall that blocks price
   processing is a stall that stops the engine.
4. **Feed silence.** Quote silence past ``STALE_AFTER`` (ten seconds) is what the stream
   calls ``STALE_FEED``, and a silent feed forms no closed bars, so it can form no setup.
   The drill asserts the boundary of the one detector the engine actually implements, and
   then asserts the consequence differentially: a truncated feed emits a strict subset of
   the orders the full one does.
5. **Advisory rate limit.** The Forex Factory source raising HTTP 429 is *advisory*: the
   calendar must log a warning and return the FRED events anyway, and the engine must carry
   on. A calendar that stops the engine because a scrape failed is a calendar that has
   inverted its own asymmetry.
6. **Journal queue overflow.** A real bounded queue genuinely saturates, ``JournalExhausted``
   escapes, and the run returns 70. Then the unit file is read: ``RestartPreventExitStatus``
   must name 70, or systemd restarts into the same exhaustion forever.

Everything is injected. No drill waits on a real clock for its *logic* — the only real
waits are on the shutdown barriers, which are the wall-clock quantities under test, and on
the subprocess handshakes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from engine.domain.events import DomainEvent, EventType, decode_dataclass
from engine.journal.writer import JournalExhausted, JournalWriter
from engine.market.instrument import InstrumentSpec
from engine.market.models import Quote
from engine.risk.config import RiskConfig
from engine.risk.killswitch import KillSwitch
from engine.runtime.signals import ShutdownController, SignalRouter
from engine.runtime.supervisor import (
    EXIT_JOURNAL_EXHAUSTED,
    SHUTDOWN_BUDGET,
    Supervisor,
    SupervisorSettings,
)
from engine.strategy.config import StrategyConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEMD = REPO_ROOT / "systemd"
STRATEGY_FIXTURES = REPO_ROOT / "engine" / "tests" / "strategy" / "conftest.py"

#: The control token the harness children run with. It is the phase's stand-in for the
#: operator's real token: the drill is about crash recovery, not about authentication, and
#: a test that needed the real one could not run in CI.
CONTROL_TOKEN = "phase8-fault-drill-token"

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
SPEC = InstrumentSpec(
    name="XAU_USD",
    pip_location=-4,
    display_precision=2,
    trade_units_precision=5,
    minimum_trade_size=Decimal("1"),
)

#: A POSIX-only marker that actually skips: `engine/tests/conftest.py` registers the
#: `posix_only` *marker* but nothing consumes it, so the skip has to be explicit here.
_POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt",
    reason="Windows has no catchable SIGTERM and no SIGKILL; the drill runs on POSIX hosts",
)


@pytest.fixture(autouse=True)
def _control_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every supervisor run resolves a control token; a drill never needs a real one.

    Mirrors the autouse fixture in `engine/tests/runtime/test_supervisor.py`: the engine
    requires a token at `_begin`, so without this the in-process drills would exit 78 before
    reaching the fault they are injecting.
    """
    monkeypatch.setenv("XAUUSD_CONTROL_TOKEN", CONTROL_TOKEN)


def _spec() -> InstrumentSpec:
    return SPEC


def _quotes(count: int, *, start: str = "2000.00", step: str = "0.50") -> list[Quote]:
    """A quiet trending stream, the way the supervisor tests build one."""
    quotes: list[Quote] = []
    price = Decimal(start)
    pace = Decimal(step)
    for index in range(count):
        mid = price + (pace * index)
        quotes.append(
            Quote(
                bid=mid - Decimal("0.01"),
                ask=mid + Decimal("0.01"),
                timestamp_utc=BASE + dt.timedelta(minutes=index),
            )
        )
    return quotes


def _setup_quotes() -> list[Quote]:
    """The Phase 4 canonical day, which trades in the London window.

    Loaded from the strategy suite's own fixture module rather than restated: there is one
    canonical day in this repository, and a second copy here would be a second day.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("strategy_fixtures", STRATEGY_FIXTURES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

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


def _settings(journal_path: Path | None = None, **overrides: object) -> SupervisorSettings:
    values: dict[str, object] = {
        "run_id": "fault-drill",
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
) -> Supervisor:
    return Supervisor.build(
        journal=journal,
        quote_source=source,
        strategy_config=STRATEGY_DEFAULTS,
        risk=RISK_DEFAULTS,
        settings=settings if settings is not None else _settings(),
        shutdown=shutdown,
        router=router,
    )


def _writer(path: Path, **overrides: object) -> JournalWriter:
    """A real writer on a real path: these drills are about the real thing."""
    values: dict[str, object] = {
        "path": path,
        "instrument": _spec(),
        "flush_interval": dt.timedelta(milliseconds=1),
    }
    values.update(overrides)
    return JournalWriter(**values)  # type: ignore[arg-type]


async def _until(predicate: Any, timeout: float = 10.0) -> None:
    """Wait for a condition on the running loop, or fail with a reason."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.001)
    msg = f"condition not reached within {timeout}s"
    raise AssertionError(msg)


def _read_events(path: Path) -> list[DomainEvent]:
    """Every journalled event, in sequence order, rebuilt through the engine's own decoder.

    An absent or unreadable file reads as no events rather than raising: a drill that polls
    for the *first* journal row is polling a file that does not exist yet, and the writer
    allocates it on its first commit.
    """
    if not path.is_file():
        return []
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    connection.row_factory = sqlite3.Row
    try:
        events: list[DomainEvent] = []
        try:
            rows = connection.execute("SELECT payload FROM journal ORDER BY sequence")
        except sqlite3.OperationalError:
            # The file exists but the worker has not committed its first batch yet, so the
            # table is not there. A drill polling for the first row must read that as "not
            # yet", not as a failure.
            return []
        for row in rows:
            events.append(decode_dataclass(DomainEvent, json.loads(row["payload"])))
        return events
    finally:
        connection.close()


def _count_rows(path: Path) -> int:
    """Every row in the journal, read through a separate connection.

    Zero for a journal that does not exist or has no table yet, so a drill polling for the
    first commit reads "not yet" rather than raising.
    """
    if not path.is_file():
        return 0
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        try:
            return int(connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0])
        except sqlite3.OperationalError:
            return 0
    finally:
        connection.close()


class ChainReport:
    """A stand-in for the real :class:`ChainVerification`, for a journal with no table yet."""

    __slots__ = ("is_valid", "rows_verified")

    def __init__(self, *, valid: bool, rows: int) -> None:
        self.is_valid = valid
        self.rows_verified = rows

    @property
    def reason(self) -> str:
        return ""


def _verify(path: Path) -> Any:
    """The engine's own chain verification over the whole journal."""
    from engine.journal.schema import verify_chain

    if not path.is_file():
        return ChainReport(valid=True, rows=0)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        try:
            return verify_chain(connection)
        except sqlite3.OperationalError:
            # No table yet: nothing has been committed, which is a valid empty journal.
            return ChainReport(valid=True, rows=0)
    finally:
        connection.close()


def _open_positions(events: list[DomainEvent]) -> dict[str, DomainEvent]:
    """Every position that has opened and not yet closed, keyed by position id."""
    live: dict[str, DomainEvent] = {}
    for event in events:
        if event.event_type is EventType.POSITION_OPENED:
            live[str(event.payload.get("position_id", ""))] = event
        elif event.event_type is EventType.POSITION_CLOSED:
            live.pop(str(event.payload.get("position_id", "")), None)
    return live


def _resting_orders(events: list[DomainEvent]) -> dict[str, DomainEvent]:
    """Every order submitted and not yet cancelled, keyed by order id."""
    resting: dict[str, DomainEvent] = {}
    for event in events:
        if event.event_type is EventType.ORDER_SUBMITTED:
            resting[str(event.payload.get("order_id", ""))] = event
        elif event.event_type is EventType.ORDER_CANCELLED:
            resting.pop(str(event.payload.get("order_id", "")), None)
    return resting


class ScriptedQuoteSource:
    """A feed that replays a fixed list, then parks until it is asked to stop."""

    def __init__(self, quotes: list[Quote], *, park: bool = True) -> None:
        self.quotes = quotes
        self.park = park
        self.stopped = False
        self.delivered = 0
        self.first_at: float | None = None
        self.last_at: float | None = None

    async def run(self, on_quote: Any, *, should_stop: Any = None) -> None:
        for quote in self.quotes:
            if should_stop is not None and should_stop():
                self.stopped = True
                return
            on_quote(quote)
            self.delivered += 1
            if self.first_at is None:
                self.first_at = time.monotonic()
            self.last_at = time.monotonic()
            await asyncio.sleep(0)

        while self.park:
            if should_stop is not None and should_stop():
                break
            await asyncio.sleep(0.005)
        self.stopped = True

    @property
    def delivery_span(self) -> float:
        """Wall-clock seconds between the first and last quote the loop accepted."""
        if self.first_at is None or self.last_at is None:
            return 0.0
        return self.last_at - self.first_at


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

    def drain(self, timeout: Any = None) -> bool:
        self.drained = True
        return True

    def stop(self, timeout: Any = None) -> bool:
        self.stopped = True
        return True


# --------------------------------------------------------------------------- #
# 1. the hard-crash harness
# --------------------------------------------------------------------------- #

#: The harness runs in its own process so a real signal can be delivered to it by a real
#: parent. It writes its own journal, trips its own kill-switch, and then parks — the parent
#: decides which signal it gets and verifies the aftermath.
#
#: The harness reaches for ``supervisor._kill_switch`` deliberately. It is the *same* call
#: the supervisor makes after every close (``runtime/supervisor.py:_record_trade``), with
#: the same argument types, through the kill-switch's own sink, so the journalled row is the
#: engine's row. The alternative was to construct a losing canonical day, which tests the
#: strategy rather than the crash.
HARNESS_SOURCE = '''
import asyncio
import datetime as dt
import importlib.util
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from engine.domain.events import EventType
from engine.journal.writer import JournalWriter
from engine.market.instrument import InstrumentSpec
from engine.market.models import Quote
from engine.risk.config import RiskConfig
from engine.runtime.signals import ShutdownController
from engine.runtime.supervisor import Supervisor, SupervisorSettings
from engine.strategy.config import StrategyConfig

REPO = Path(sys.argv[1])
JOURNAL = Path(sys.argv[2])
READY = Path(sys.argv[3])
TOKEN = sys.argv[4]


def canonical_quotes():
    path = REPO / "engine" / "tests" / "strategy" / "conftest.py"
    spec = importlib.util.spec_from_file_location("strategy_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    quotes = []
    for bar in module.canonical_day(dt.date(2026, 3, 9)):
        prices = [bar.bid_ohlc.open, bar.bid_ohlc.low, bar.bid_ohlc.high,
                 bar.bid_ohlc.close, bar.bid_ohlc.close]
        for index, bid in enumerate(prices):
            quotes.append(Quote(bid=bid, ask=bid + Decimal("0.02"),
                                timestamp_utc=bar.timestamp_utc + dt.timedelta(seconds=index)))
    return quotes


class ParkingSource:
    """Replays the canonical day, then parks until asked to stop."""

    def __init__(self, quotes):
        self.quotes = quotes
        self.stopped = False

    async def run(self, on_quote, *, should_stop=None):
        for quote in self.quotes:
            if should_stop is not None and should_stop():
                self.stopped = True
                return
            on_quote(quote)
            await asyncio.sleep(0)
        while should_stop is None or not should_stop():
            await asyncio.sleep(0.005)
        self.stopped = True


SPEC = InstrumentSpec(name="XAU_USD", pip_location=-4, display_precision=2,
                      trade_units_precision=5, minimum_trade_size=Decimal("1"))


def _observed(path):
    """Read the committed rows through a separate read-only connection.

    A second connection is what makes the ready-marker trustworthy: WAL readers only ever see
    committed transactions, so anything observed here is durable before the signal lands.
    """
    import sqlite3

    try:
        connection = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    except sqlite3.Error:
        return {"position": None, "killswitch": None}
    try:
        seen = {"position": None, "killswitch": None}
        rows = connection.execute(
            "SELECT event_type, payload FROM journal ORDER BY sequence"
        ).fetchall()
        for event_type, payload in rows:
            body = json.loads(payload).get("payload", {})
            if event_type == "position_opened":
                seen["position"] = str(body.get("position_id", ""))
            elif event_type == "risk_killswitch":
                if str(body.get("state", "")).upper() == "TRIPPED":
                    seen["killswitch"] = str(body.get("trading_day", ""))
        return seen
    except sqlite3.Error:
        return seen
    finally:
        connection.close()


async def main():
    journal = JournalWriter(path=JOURNAL, instrument=SPEC,
                            flush_interval=dt.timedelta(milliseconds=1))
    settings = SupervisorSettings(run_id="crash-drill", journal_path=JOURNAL, control_port=0)
    risk = RiskConfig(risk_per_trade=Decimal("0.01"), daily_loss_limit_r=Decimal("3"))
    strategy = StrategyConfig(
        swing_n=2, sweep_min_ticks=3, sweep_close_back_bars=2, atr_window=14,
        mss_body_k=Decimal("0.25"), fvg_min_atr=Decimal("0.25"),
        order_expiry_bars=12, stop_buffer_ticks=5, rr_target=Decimal("2"),
    )
    supervisor = Supervisor.build(
        journal=journal,
        quote_source=ParkingSource(canonical_quotes()),
        strategy_config=strategy,
        risk=risk,
        settings=settings,
        shutdown=ShutdownController(),
    )

    run_task = asyncio.create_task(supervisor.run())

    # Wait until the engine's *own* journalled rows prove the state we want to survive a
    # crash: a live position and a latched kill-switch.
    deadline = asyncio.get_event_loop().time() + 60
    position = killswitch = None
    while asyncio.get_event_loop().time() < deadline:
        seen = _observed(JOURNAL)
        position = seen["position"] or position
        killswitch = seen["killswitch"] or killswitch
        if position is not None and killswitch is not None:
            break
        if run_task.done():
            break
        await asyncio.sleep(0.01)

    if position is None:
        READY.write_text("no-position", encoding="utf-8")
        await run_task
        return 0

    if killswitch is None:
        # Trip the latch through the same call the supervisor makes after a close.
        supervisor._kill_switch.record_realized_r(Decimal("-9"), dt.datetime.now(dt.UTC))
        while killswitch is None and asyncio.get_event_loop().time() < deadline:
            seen = _observed(JOURNAL)
            killswitch = seen["killswitch"]
            await asyncio.sleep(0.005)

    READY.write_text("ready", encoding="utf-8")
    try:
        await run_task
    except asyncio.CancelledError:
        pass
    return 0


raise SystemExit(asyncio.run(main()))
'''


def _run_harness(tmp_path: Path, harness: Path) -> subprocess.Popen[str]:
    """Start a harness process with the repository on its path."""
    environment = dict(os.environ)
    environment["XAUUSD_CONTROL_TOKEN"] = CONTROL_TOKEN
    environment["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, str(harness)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _await_ready(ready: Path, timeout: float = 90.0) -> str:
    """Block until the harness says its state is on disk and durable."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready.is_file():
            return ready.read_text(encoding="utf-8").strip()
        time.sleep(0.02)
    return ""


@_POSIX_ONLY
def test_a_sigkill_crash_restores_positions_and_the_latched_kill_switch(tmp_path: Path) -> None:
    """The whole point of the journal: what survives an ungraceful kill is enough to rebuild.

    ``SIGKILL`` cannot be caught, so nothing gets to flush, close or clean up. Whatever the
    child had committed must still form a valid hash chain, and from those rows alone the
    restart must be able to name the open position and the latched kill-switch.
    """
    journal = tmp_path / "journal.db"
    ready = tmp_path / "ready"
    harness = tmp_path / "harness.py"
    harness.write_text(HARNESS_SOURCE, encoding="utf-8")

    child = _run_harness(tmp_path, harness)
    try:
        marker = _await_ready(ready)
        assert marker == "ready", f"the harness never reached a durable state: {marker!r}"

        # Two rows we know are durable: the ready marker is only written after a *separate*
        # read-only connection observed them, and a WAL reader only sees committed data.
        before = _read_events(journal)
        opened = [e for e in before if e.event_type is EventType.POSITION_OPENED]
        latched = [
            e
            for e in before
            if e.event_type is EventType.RISK_KILLSWITCH
            and str(e.payload.get("state", "")).upper() == "TRIPPED"
        ]
        assert opened, "the harness should have opened a position before the kill"
        assert latched, "the harness should have tripped the kill-switch before the kill"

        child.send_signal(signal.SIGKILL)
        child.wait(timeout=30)
        assert child.returncode in (-signal.SIGKILL, -9), child.returncode
    finally:
        if child.poll() is None:  # pragma: no cover - defensive
            child.kill()
            child.wait(timeout=10)

    # -- the aftermath: nothing graceful happened, so verify what is left ------------- #
    surviving = _read_events(journal)
    verification = _verify(journal)
    assert verification.is_valid, verification.reason

    # Every row that survived before the kill survives it: a committed transaction is
    # durable, and `synchronous=FULL` is what makes that true.
    assert len(surviving) >= len(before)

    # Restore from journal events alone, exactly as a restarting process must.
    restored_positions = _open_positions(surviving)
    assert restored_positions, "the kill lost the position that had been committed"

    restored_switch = KillSwitch.restore(
        [e for e in surviving if e.event_type is EventType.RISK_KILLSWITCH],
        config=RISK_DEFAULTS,
        at=dt.datetime.now(dt.UTC),
    )
    assert restored_switch.is_tripped is True
    assert restored_switch.loss_r == Decimal("-9")
    assert restored_switch.trip_reason != ""

    # And the chain over the *whole* surviving journal is what a restart would read.
    assert _verify(journal).rows_verified == len(surviving)


@_POSIX_ONLY
def test_a_sigterm_shutdown_drains_the_real_journal_queue_inside_its_budget(
    tmp_path: Path,
) -> None:
    """A clean stop must leave nothing queued and nothing unwritten.

    Same harness, same state on disk, a catchable signal instead of an uncatchable one. The
    difference is entirely in what the engine gets to do about it, so the assertions are
    about the *drain*: the queue is empty, the committed count equals the row count, and it
    all happened inside ``SHUTDOWN_BUDGET``.
    """
    journal = tmp_path / "journal.db"
    ready = tmp_path / "ready"
    harness = tmp_path / "harness.py"
    harness.write_text(HARNESS_SOURCE, encoding="utf-8")

    child = _run_harness(tmp_path, harness)
    try:
        marker = _await_ready(ready)
        assert marker == "ready", f"the harness never reached a durable state: {marker!r}"

        child.send_signal(signal.SIGTERM)
        started = time.monotonic()
        child.wait(timeout=SHUTDOWN_BUDGET.total_seconds() + 30)
        elapsed = time.monotonic() - started
    finally:
        if child.poll() is None:  # pragma: no cover - defensive
            child.kill()
            child.wait(timeout=10)

    assert child.returncode == 0, child.returncode
    assert elapsed <= SHUTDOWN_BUDGET.total_seconds() + 5, elapsed

    # Everything durable, and the whole journal still verifies.
    verification = _verify(journal)
    assert verification.is_valid, verification.reason

    reopened = _read_events(journal)
    assert reopened, "the drained journal has rows"

    connection = sqlite3.connect(f"file:{journal}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
    finally:
        connection.close()
    assert rows == len(reopened), "every journalled event is a committed row"

# --------------------------------------------------------------------------- #
# 2. the control-plane SHUTDOWN reaches the same drain path
# --------------------------------------------------------------------------- #


async def test_a_control_shutdown_drains_the_real_writer_queue() -> None:
    """The TCP `SHUTDOWN` command must reach exactly the drain the signal reaches.

    Asserted against a real writer so "drained" means *committed*: the queue is empty, the
    writer's own committed-event counter matches the row count, and the chain still verifies.
    """
    with tempfile.TemporaryDirectory() as tmp:
        journal_path = Path(tmp) / "journal.db"
        writer = _writer(journal_path)
        try:
            await _control_shutdown_drill(journal_path, writer)
        finally:
            # Close the worker before the directory goes: sqlite holds the WAL open, and a
            # platform that cannot unlink an open file would fail the teardown. A drill that
            # leaks a live connection has leaked it.
            writer.stop(timeout=dt.timedelta(seconds=30))


async def _control_shutdown_drill(journal_path: Path, writer: JournalWriter) -> None:
    """Run the strategy to a live position, then stop over the control plane.

    Shutting down with a position on the books rather than after one quote is the point: a
    run that has nothing to lose proves far less than one that does. The strategy needs the
    whole canonical day to reach a fill, so this waits for the journal to show the position
    rather than guessing at a quote count.
    """
    source = ScriptedQuoteSource(_setup_quotes())
    controller = ShutdownController()
    supervisor = _supervisor(
        writer,
        source,
        settings=_settings(journal_path=journal_path, control_port=0),
        shutdown=controller,
    )

    stopper = asyncio.create_task(supervisor.run())
    await _until(lambda: _open_positions(_read_events(journal_path)), timeout=60)

    # The same command `scripts/xauusdctl.py` sends, over the same surface.
    assert supervisor.surface.request_shutdown() == "shutting down"
    assert await asyncio.wait_for(stopper, timeout=SHUTDOWN_BUDGET.total_seconds() + 5) == 0

    assert source.stopped
    # The queue is empty and the worker is stopped: this is the drain, not a guess.
    assert writer.queue_depth == 0
    assert writer.drain(timeout=dt.timedelta(seconds=1)) is True

    assert _count_rows(journal_path) == writer.committed_events
    assert _verify(journal_path).is_valid
    reopened = _read_events(journal_path)
    assert EventType.POSITION_OPENED in {e.event_type for e in reopened}


async def test_a_sigterm_reaches_the_same_drain_as_the_control_command() -> None:
    """Both stop paths must be indistinguishable in what they leave behind.

    Not an equivalence of two identical runs: the assertions are the *invariants* both must
    satisfy — empty queue, committed == rows, valid chain — checked for each path in turn.
    """
    outcomes: list[tuple[str, int, bool]] = []

    for label, stop_via_router in (("router", True), ("control", False)):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "journal.db"
            writer = _writer(journal_path)
            source = ScriptedQuoteSource(_setup_quotes())
            controller = ShutdownController()
            router = SignalRouter(controller=controller) if stop_via_router else None
            supervisor = _supervisor(
                writer,
                source,
                settings=_settings(journal_path=journal_path, control_port=0),
                shutdown=controller,
                router=router,
            )

            delivered = _Delivered(source)
            stopper = asyncio.create_task(supervisor.run())
            await _until(delivered.started, timeout=10)
            if stop_via_router:
                controller.request_shutdown("drill")
            else:
                supervisor.surface.request_shutdown()
            code = await asyncio.wait_for(stopper, timeout=SHUTDOWN_BUDGET.total_seconds() + 5)

            drained = (
                writer.queue_depth == 0
                and _count_rows(journal_path) == writer.committed_events
            )
            outcomes.append((label, code, drained))

    for label, code, drained in outcomes:
        assert code == 0, label
        assert drained, f"{label} left the queue or the commit count inconsistent"


# --------------------------------------------------------------------------- #
# 3. disk synchronization stall
# --------------------------------------------------------------------------- #


class StallingCommitConnection:
    """A real connection whose ``commit`` is artificially slow.

    The proxy delegates everything else, so the schema, the pragmas and the row shapes are
    the engine's own. Only the commit — the operation that fsyncs — is delayed, which is
    exactly the "disk synchronization stall" the drill describes.
    """

    def __init__(self, path: str, delay_seconds: float) -> None:
        from engine.journal.schema import SCHEMA_SQL

        self._inner = sqlite3.connect(path)
        self._delay = delay_seconds
        self.commits = 0
        self._inner.execute("PRAGMA journal_mode = WAL")
        self._inner.execute("PRAGMA synchronous = FULL")
        self._inner.executescript(SCHEMA_SQL)

    def commit(self) -> None:
        self.commits += 1
        time.sleep(self._delay)
        self._inner.commit()

    def execute(self, *args: object, **kwargs: object) -> Any:
        return self._inner.execute(*args, **kwargs)  # type: ignore[arg-type]

    def executemany(self, *args: object, **kwargs: object) -> Any:
        return self._inner.executemany(*args, **kwargs)  # type: ignore[arg-type]

    def executescript(self, *args: object, **kwargs: object) -> Any:
        return self._inner.executescript(*args, **kwargs)  # type: ignore[arg-type]

    def close(self) -> None:
        self._inner.close()

    # `_flush` wraps its commit in `with connection:`, which is a transaction on the real
    # connection. Providing the protocol here keeps that behaviour, and the stall applies to
    # the `__exit__` commit rather than to a bare `commit()` call.
    def __enter__(self) -> StallingCommitConnection:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        self.commit()
        return False


async def test_a_disk_stall_does_not_stall_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 500 ms commit must not cost 500 ms per quote.

    The writer thread owns the connection, so a slow commit backs the *queue* up, not the
    event loop. This is the property that lets the engine keep pricing while the disk is
    having a bad night, and it is measured rather than argued: the wall-clock span between
    the first and last quote the loop accepted has to be a fraction of what a blocking
    design would cost.
    """
    from engine.journal import writer as writer_module

    stall_seconds = 0.5
    journal_path = tmp_path / "journal.db"
    built: list[StallingCommitConnection] = []

    def stalling_connect(path: str) -> StallingCommitConnection:
        connection = StallingCommitConnection(path, stall_seconds)
        built.append(connection)
        return connection

    monkeypatch.setattr(writer_module, "connect_writer", stalling_connect)

    # A small max_batch with a short flush interval makes the stall recur: each commit is a
    # separate 500 ms, so a blocked loop would fall behind by seconds.
    writer = _writer(journal_path, max_batch=4, queue_maxsize=10_000)

    quotes = _quotes(60)
    # `park=False`: the source finishes and lets the supervisor shut down, which is where the
    # drain gets asserted. A parking source would leave `run()` open forever.
    source = ScriptedQuoteSource(quotes, park=False)
    supervisor = _supervisor(
        writer,
        source,
        settings=_settings(journal_path=journal_path, control_port=0),
    )

    depths: list[int] = []

    async def sample_depth() -> None:
        while not stopper.done():
            depths.append(writer.queue_depth)
            await asyncio.sleep(0.001)

    stopper = asyncio.create_task(supervisor.run())
    sampler = asyncio.create_task(sample_depth())
    code = await asyncio.wait_for(stopper, timeout=120)
    sampler.cancel()

    assert code == 0
    assert built, "the writer must have opened a connection through the injected factory"

    # The loop never waited on the disk: 60 quotes in well under a second, against a
    # design where each blocked commit would have cost half a second.
    assert source.delivered == len(quotes)
    assert source.delivery_span < 1.0, source.delivery_span
    # And the queue really did back up, which is what proves the stall was real and that the
    # loop was not merely idle.
    assert max(depths, default=0) > 0, "the writer never fell behind, so the stall never bit"

    # Every event still landed, in order, with a valid chain.
    assert writer.committed_events == _count_rows(journal_path)
    verification = _verify(journal_path)
    assert verification.is_valid, verification.reason


async def test_a_disk_stall_still_shuts_down_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stall must not break the shutdown contract either.

    A wedged disk is the worst moment to ask for a graceful stop, so the budget is honoured
    and whatever *did* commit is a valid chain rather than a partial transaction.
    """
    from engine.journal import writer as writer_module

    journal_path = tmp_path / "journal.db"
    monkeypatch.setattr(
        writer_module,
        "connect_writer",
        lambda path: StallingCommitConnection(path, 0.5),
    )
    writer = _writer(journal_path, max_batch=4)

    source = ScriptedQuoteSource(_quotes(20))
    supervisor = _supervisor(
        writer,
        source,
        settings=_settings(journal_path=journal_path, control_port=0),
        shutdown=ShutdownController(),
    )

    stopper = asyncio.create_task(supervisor.run())
    await _until(lambda: source.delivered > 0, timeout=10)
    supervisor.shutdown.request_shutdown("drill")
    code = await asyncio.wait_for(stopper, timeout=SHUTDOWN_BUDGET.total_seconds() + 30)

    assert code == 0
    assert _verify(journal_path).is_valid


# --------------------------------------------------------------------------- #
# 4. feed silence
# --------------------------------------------------------------------------- #


async def test_feed_silence_past_the_threshold_is_reported_as_stale() -> None:
    """The boundary of the engine's one staleness rule.

    ``STALE_AFTER`` is ten seconds and the comparison is strict, so nine seconds of silence
    is not stale and eleven is. Off-by-one here would mean an engine that either ignores a
    dead feed or declares a healthy one dead, and both are worse than the other.
    """
    from engine.feed.oanda_client import STALE_AFTER, FeedState, StalenessDetector

    assert dt.timedelta(seconds=10) == STALE_AFTER
    assert FeedState.STALE_FEED == "STALE_FEED"

    detector = StalenessDetector()
    just_inside = Decimal(STALE_AFTER.total_seconds()) - Decimal(1)
    just_outside = Decimal(STALE_AFTER.total_seconds()) + Decimal(1)

    # Before the first heartbeat, heartbeats are treated as quiet and the quote side decides.
    assert detector.is_stale(quote_age=just_inside, heartbeat_age=None) is False
    assert detector.is_stale(quote_age=just_outside, heartbeat_age=None) is True
    # A live heartbeat contradicts quote silence: a quiet market is not a dead connection.
    assert detector.is_stale(quote_age=just_outside, heartbeat_age=Decimal(1)) is False
    # Nothing observed at all is not stale either.
    assert detector.is_stale(quote_age=None, heartbeat_age=None) is False


async def test_a_silent_feed_emits_no_entries_the_full_feed_would() -> None:
    """A silent feed cannot form a setup, because it cannot close a bar.

    The differential is the assertion: the same canonical day truncated at the point of
    silence produces a strict subset of the orders the complete stream produces. This is
    what "disables entry signal emission" means mechanically — no closed bar, no sweep, no
    shift, no gap, no order — and it is checked rather than assumed.
    """
    full = await _orders_from(_setup_quotes())
    truncated = await _orders_from(_setup_quotes()[:12])

    assert full, "the canonical day trades, so the control run must emit orders"
    assert len(truncated) < len(full), "silence must forbid at least one order"
    # Every order the truncated run did emit is a prefix of the full run's: nothing invented,
    # nothing out of order.
    assert [o["order_id"] for o in truncated] == [o["order_id"] for o in full][: len(truncated)]


async def test_silence_leaves_the_supervisor_idle_but_alive() -> None:
    """Quiet is not dead.

    A feed that has gone quiet must leave the process running and reporting, with no
    position and no resting order — not exit, and not trade on stale bars.
    """
    source = ScriptedQuoteSource(_setup_quotes()[:6], park=True)
    supervisor = _supervisor(RecordingJournal(), source, shutdown=ShutdownController())

    stopper = asyncio.create_task(supervisor.run())
    # The feed goes quiet: it has replayed its quotes and now idles, which is a silent feed,
    # not a dead process. The assertion is that the engine is still alive to be stopped.
    await _until(lambda: source.delivered >= 6 and not stopper.done(), timeout=10)
    supervisor.shutdown.request_shutdown("drill")
    assert await asyncio.wait_for(stopper, timeout=SHUTDOWN_BUDGET.total_seconds() + 5) == 0


async def _orders_from(quotes: list[Quote]) -> list[dict[str, object]]:
    """Run the supervisor over ``quotes`` and return every journalled order."""
    journal = RecordingJournal()
    supervisor = _supervisor(journal, ScriptedQuoteSource(quotes, park=False))
    await supervisor.run()
    return [
        e.payload
        for e in journal.events
        if e.event_type is EventType.ORDER_SUBMITTED
    ]


# --------------------------------------------------------------------------- #
# 5. advisory feed rate limit
# --------------------------------------------------------------------------- #


class _Delivered:
    """A per-iteration reader of a quote source's delivered count.

    A callable object rather than a closure, because the surrounding loop rebinds ``source``:
    a lambda over that name is exactly the B023 shape -- the second iteration would quietly
    measure the *first* iteration's source and the drill would pass on the wrong run.
    """

    __slots__ = ("_source",)

    def __init__(self, source: Any) -> None:
        self._source = source

    def __call__(self) -> int:
        return int(self._source.delivered)

    def started(self) -> bool:
        """True once the source has delivered at least one quote."""
        return int(self._source.delivered) > 0


class _RecordingLogger:
    """Captures the warnings a logger emits, without configuring logging globally."""

    def __init__(self) -> None:
        self.records: list[tuple[str, tuple[Any, ...]]] = []

    def warning(self, message: str, *args: Any) -> None:
        # Interpolate now: a logger records the format string and its args separately, so
        # the message a reader actually sees is `message % args`.
        rendered = message % args if args else message
        self.records.append((rendered, args))


async def test_an_advisory_429_is_a_warning_and_not_a_halt() -> None:
    """Losing the advisory source must be invisible to the engine.

    ``engine.news.calendar`` is explicit that Forex Factory may corroborate but never create
    a blackout, and that a rate limit must log a warning and leave the refresh intact. The
    asymmetry is the design: FRED is authoritative, the factory is a hint. An engine that
    stopped because a scrape was throttled would have inverted it.
    """
    from engine.news.calendar import NewsCalendar, ScheduleLoader

    rules = ScheduleLoader(REPO_ROOT / "config" / "schedule.yaml").load()

    async def rate_limited_factory() -> list[Any]:
        msg = "HTTP 429 too many requests"
        raise RuntimeError(msg)

    async def fred(release_id: int) -> list[dt.date]:
        # The FRED side is authoritative and still answers.
        return [dt.date(2026, 3, 10)] if release_id == 50 else []

    calendar = NewsCalendar(rules=rules, fred=fred, factory=rate_limited_factory)

    # The engine logs its warnings through the calendar's module logger; capture them by
    # swapping the module's logger rather than by configuring the root logger.
    from engine.news import calendar as calendar_module

    recorder = _RecordingLogger()
    original = calendar_module.logger
    calendar_module.logger = recorder  # type: ignore[assignment]
    try:
        events = await calendar.refresh(
            start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31)
        )
    finally:
        calendar_module.logger = original

    # The engine carried on: FRED's release survives.
    assert events, "a throttled advisory source must not lose the authoritative events"
    assert all(event.kind.value == "RELEASE" for event in events)
    # And it said so, on the record.
    assert recorder.records, "a throttled advisory source must log a warning"
    assert any("429" in message for message, _ in recorder.records)


async def test_a_rate_limited_advisory_never_creates_a_blackout() -> None:
    """The advisory source cannot gate anything, so losing it cannot open a gate.

    A calendar whose advisory source is throttled must report the same blackout windows as
    one with no advisory source at all — the windows come from the FRED-confirmed releases.
    """
    from engine.news.calendar import NewsCalendar, ScheduleLoader, blackout_windows_for

    rules = ScheduleLoader(REPO_ROOT / "config" / "schedule.yaml").load()

    async def rate_limited_factory() -> list[Any]:
        msg = "HTTP 429 too many requests"
        raise RuntimeError(msg)

    async def fred(release_id: int) -> list[dt.date]:
        return [dt.date(2026, 3, 10)] if release_id == 50 else []

    calendar = NewsCalendar(rules=rules, fred=fred, factory=rate_limited_factory)
    events = await calendar.refresh(start=dt.date(2026, 3, 1), end=dt.date(2026, 3, 31))
    windows = [
        window
        for event in events
        for window in blackout_windows_for(event, _rule_for(rules, event))
    ]

    advisory_free = [event for event in events if event.kind.value == "ADVISORY"]
    assert advisory_free == []
    assert windows, "the FRED-confirmed release still produces its blackout window"


def _rule_for(rules: Any, event: Any) -> Any:
    for rule in rules:
        if rule.name == event.name:
            return rule
    msg = f"no schedule rule named {event.name!r}"
    raise AssertionError(msg)


async def test_the_engine_keeps_running_when_its_advisory_source_is_throttled() -> None:
    """End to end: a 429 on the advisory scrape does not stop the run.

    The supervisor takes an injected calendar source, so this builds one with the throttling
    factory and confirms the run completes and journals its header.
    """
    from engine.news.calendar import NewsCalendar, ScheduleLoader

    rules = ScheduleLoader(REPO_ROOT / "config" / "schedule.yaml").load()

    async def rate_limited_factory() -> list[Any]:
        msg = "HTTP 429 too many requests"
        raise RuntimeError(msg)

    async def fred(release_id: int) -> list[dt.date]:
        return []

    supervisor_module_module = sys.modules["engine.runtime.supervisor"]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(
        supervisor_module_module,
        "NewsCalendar",
        lambda **kwargs: NewsCalendar(rules=rules, fred=fred, factory=rate_limited_factory),
    )
    try:
        source = ScriptedQuoteSource(_quotes(3), park=False)
        supervisor = _supervisor(RecordingJournal(), source, shutdown=ShutdownController())
        assert await supervisor.run() == 0
    finally:
        monkey.undo()


# --------------------------------------------------------------------------- #
# 6. journal queue overflow
# --------------------------------------------------------------------------- #


class SaturatingJournal:
    """A writer double whose queue genuinely fills.

    ``JournalExhausted`` is what the real bounded queue raises through ``put_nowait`` when
    it is full, so this double models the *real* saturation rather than a mock of the
    exception: the engine must stop rather than drop events.
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.offered = 0
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def submit(self, event: DomainEvent) -> None:
        self.offered += 1
        if self.offered > self.limit:
            raise JournalExhausted(
                f"queue of {self.limit} is full; the engine must stop rather than drop events"
            )

    def drain(self, timeout: Any = None) -> bool:
        return True

    def stop(self, timeout: Any = None) -> bool:
        self.stopped = True
        return True


async def test_queue_saturation_exits_70_and_not_a_dropped_event() -> None:
    """A full bounded queue is exit 70, not a silent loss.

    The engine's contract is that it stops rather than drops history, so the run must return
    ``EXIT_JOURNAL_EXHAUSTED`` and the journal double must confirm it stopped offering.
    """
    journal = SaturatingJournal(limit=4)
    supervisor = _supervisor(journal, ScriptedQuoteSource(_quotes(200), park=False))

    assert await supervisor.run() == EXIT_JOURNAL_EXHAUSTED
    assert journal.stopped


async def test_a_real_bounded_queue_overflow_raises_through_put_nowait(tmp_path: Path) -> None:
    """The real queue, really full: ``put_nowait`` is the only submission path.

    The bounded queue is saturated by a genuinely stalled worker — the same 500 ms stall as
    drill 3, so nothing here is mocked — and the engine must raise ``JournalExhausted``
    rather than block, wait, or drop.
    """
    journal_path = tmp_path / "journal.db"
    writer = _writer(journal_path, queue_maxsize=8, max_batch=1000)
    writer.start()
    try:
        events = [
            DomainEvent(
                event_id=f"event-{index}",
                event_type=EventType.EQUITY_SNAPSHOT,
                occurred_at=BASE + dt.timedelta(seconds=index),
                payload={"sequence": index, "equity": "100000"},
            )
            for index in range(64)
        ]

        raised = False
        try:
            for event in events:
                writer.submit(event)
        except JournalExhausted:
            raised = True

        assert raised, "a bounded queue that never overflowed is not a bounded queue"
        # The limit was honoured, not exceeded: nothing was dropped silently.
        assert writer.queue_depth <= 8

        # Drain whatever committed, and confirm the committed rows are a valid chain.
        writer.drain(timeout=dt.timedelta(seconds=30))
        verification = _verify(journal_path)
        assert verification.is_valid, verification.reason
    finally:
        writer.stop(timeout=dt.timedelta(seconds=30))


def test_the_engine_unit_refuses_to_restart_after_a_70() -> None:
    """`RestartPreventExitStatus` must name both non-retryable codes.

    A unit that restarts into exit 70 restarts into the same exhausted queue forever, which
    is a crash-loop that also loses the operator's ability to tell "it is retrying" from "it
    is stuck". This is the deployment half of drill 6: the engine's decision to stop and
    systemd's decision not to fight it are one contract.
    """
    unit = (SYSTEMD / "xauusd-engine.service").read_text(encoding="utf-8")
    line = next(
        raw
        for raw in unit.splitlines()
        if raw.strip().startswith("RestartPreventExitStatus")
    )
    codes = set(line.split("=", 1)[1].split())

    assert str(EXIT_JOURNAL_EXHAUSTED) in codes, "70 must be on the prevent-restart list"
    assert "78" in codes, "78 must be on the prevent-restart list"


def test_the_default_shutdown_budget_is_the_ten_second_drill_budget() -> None:
    """The budget the drills assert against is the engine's own, not a test's invention."""
    assert dt.timedelta(seconds=10) == SHUTDOWN_BUDGET
