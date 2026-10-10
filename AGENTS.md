# AGENTS.md

This file is the authoritative operating guide for any AI agent or human contributor working on
this repository. Read it fully before writing code. When this file conflicts with a request,
the invariants in Section 2 win — raise the conflict instead of silently violating them.

---

## 0. Project Summary

A research/production lab for an automated **XAUUSD (gold) trading engine**:

- Python engine (`engine/`) that consumes OANDA market data, runs ICT-based strategy logic,
  simulates pessimistic order fills, journals every event to an isolated SQLite WAL store, and
  publishes projections for a dashboard.
- React + Vite dashboard (`web/`) and a read-only FastAPI service (`api/`).
- Deployed via `systemd` units on Linux, `uv`-managed toolchain throughout.

Development proceeds in **strict, isolated phases**. You work on exactly one phase at a time.

---

## 1. Operating Rules

1. **Phase Isolation**
   - Only write code for the `CURRENT ACTIVE PHASE` declared in Section 4.
   - Do **not** generate stub files, placeholder classes, `TODO` skeletons, or speculative imports
     for future phases.
   - Do **not** "get ahead" by scaffolding modules that a later phase owns.

2. **Verification First**
   - Before declaring any phase or task complete, you must run the designated test suite via
     `uv run pytest` and confirm **all** assertions pass.
   - A phase is not done because the code compiles, looks right, or runs once. It is done when the
     tests pass.
   - Report the actual command output; never claim a test suite passed without running it.

3. **No Hallucinated Simplifications**
   - Follow the architectural constraints in Section 2 strictly.
   - Do not replace explicit thread models with naive `asyncio` shortcuts.
   - Do not swap `Decimal` for `float`, an OS-thread worker for `asyncio.to_thread`, or a real
   pessimistic fill model for optimistic assumptions.

---

## 2. Mandatory Technical Invariants

These are non-negotiable. Violating any of them is a defect, even if tests still pass.

### 2.1 Zero Floating-Point Arithmetic
- Use Python `Decimal` or integer ticks exclusively for: currency, balances, lot sizes, fees,
  prices, and PnL.
- Floats are strictly prohibited in domain models.
- Convert to `float` only at the final display/serialization boundary, never in computation.
- Quantize and round explicitly (`ROUND_HALF_EVEN` unless a venue mandates otherwise) and centralize
  rounding helpers instead of scattering ad-hoc `.quantize()` calls.

### 2.2 Isolated SQLite Journal Worker
- All SQLite write transactions execute on a **dedicated background OS worker thread** pulling from
  a thread-safe `queue.Queue`.
- The `asyncio` event loop may only submit events via `put_nowait`.
- **Never** execute SQLite commits directly inside the async event loop.
- The worker thread owns the connection; it handles startup ordering, graceful drain on shutdown,
  and reconnection/error recovery.

### 2.3 SQLite Pragmas & Integrity
- `PRAGMA journal_mode = WAL;`
- `PRAGMA synchronous = FULL;`
- Enforce **row-level SHA-256 hash chaining** on all immutable events (each row's hash commits over
  the previous row's hash + canonical payload), so tampering is detectable.
- Schema migrations must be forward-only and idempotent.

### 2.4 Cross-Platform IPC
- Use standard `socket.AF_UNIX`.
- On macOS and Windows dev hosts, resolve socket paths to shallow directories to avoid the macOS
  104-byte `sun_path` limit:
  - `/tmp/xauusd.sock`
  - `Path(tempfile.gettempdir()) / "xauusd.sock"`
- Validate the resolved path length at bind time and fail loudly rather than crashing deep in `bind()`.

### 2.5 Pessimistic Order Resolution
- Limit fills require the price to trade **through** by at least 1 tick ($0.01).
- If an intra-bar touch spans both a resting entry and its stop-loss, assume the entry filled and
  was **immediately stopped out** in the same bar.
- Never assume favorable sequencing (entry-then-target before stop).

### 2.6 Timezone Handling
- Always resolve New York sessions via `zoneinfo.ZoneInfo("America/New_York")`.
- `tzdata` is a pinned dependency; do not rely on the host OS timezone database.
- Store all timestamps as UTC internally; convert to America/New_York only for session math.

---

## 3. Standard Repository Layout

```
xauusd-lab/
├── engine/
│   ├── src/engine/
│   │   ├── feed/        # OANDA stream & backfill
│   │   ├── market/      # Quotes, bars, session math
│   │   ├── news/        # Calendar parser & volatility breakers
│   │   ├── domain/      # Immutable events, Decimal ledger
│   │   ├── execution/   # Simulated pessimistic fill model
│   │   ├── risk/        # Drawdown limits & kill-switches
│   │   ├── strategy/    # ICT detectors & state machines
│   │   ├── journal/     # Thread-isolated SQLite WAL & hash-chain
│   │   ├── publisher/   # Projection database & static JSON exports
│   │   └── runtime/     # Supervisor, AF_UNIX listener, systemd stubs
│   └── tests/           # Unit, property, and fault injection tests
├── web/                 # React + Vite dashboard
├── api/                 # Read-only FastAPI service
├── systemd/             # Linux service unit files
└── pyproject.toml       # uv managed toolchain
```

Responsibilities are layered and directional. Do not create cross-cutting imports that bypass a
layer boundary (e.g. `strategy/` reading from `journal/`, or `publisher/` writing to raw broker
connections). Dependencies flow inward toward `domain/`.

---

## 4. Current State

> **CURRENT ACTIVE PHASE: Phase 5 (Runtime Supervisor, Control Plane & Projections)**

Phase 5 scope, and only Phase 5 scope:

- `runtime/signals.py`: cross-platform signal router. POSIX uses `loop.add_signal_handler`
  for SIGTERM/SIGINT; Windows uses `signal.signal` plus `loop.call_soon_threadsafe`. Both
  funnel into one `ShutdownController`.
- `runtime/systemd.py`: `sd_notify` wrapper (`READY=1`, `WATCHDOG=1`, `STATUS=`), a no-op
  when `NOTIFY_SOCKET` is absent or on Windows. A watchdog ping is emitted only when the
  event loop, the journal thread, and the bounded queue are all healthy.
- `runtime/control.py`: loopback TCP control plane on `127.0.0.1` via `asyncio.start_server`,
  newline-delimited JSON, token validated with `hmac.compare_digest`. Commands: STATUS,
  PAUSE, RESUME, KILL_FLATTEN, SHUTDOWN. Non-loopback peers are refused.
- `runtime/supervisor.py`: the headless async orchestrator wiring feed, bar aggregation,
  news breakers, the Phase 4 strategy, Phase 3 risk controls and the Phase 2 journal queue.
  Exit 78 for config/gate failure, exit 70 for journal exhaustion, graceful shutdown that
  drains the write queue and exits inside 10 seconds.
- `publisher/outbox.py`: an independent projection process reading the journal read-only
  (`mode=ro`), computing metrics, and writing atomic JSON snapshots (`state.json`,
  `trades.json`, `metrics.json`) through temp files and `os.replace`.
- `api/main.py`: read-only FastAPI service on `127.0.0.1:8080` serving `/api/status`,
  `/api/trades`, `/api/metrics`, `/api/health` from those snapshots with
  `Cache-Control: public, max-age=10`, mounting `web/dist` when present.
- `scripts/xauusdctl.py`: CLI administration client for the loopback control port.

**Phase 5 outcomes and the invariants that now have machine coverage:**

| Module | Invariant enforced by tests |
| --- | --- |
| `runtime/signals.py` | both platforms funnel into one controller; a POSIX install restores the previous handlers on uninstall |
| `runtime/systemd.py` | no ping when a dependency is unhealthy; no datagram without `NOTIFY_SOCKET`; Windows falls back to a no-op |
| `runtime/control.py` | a wrong token is refused by constant-time comparison; non-loopback peers are refused; every command answers exactly one line |
| `runtime/supervisor.py` | a gate/config failure exits 78, journal exhaustion exits 70, a trapped signal drains the queue and exits inside 10 seconds; no SQLite commit runs on the event loop |
| `publisher/outbox.py` | the projection reads `mode=ro` and takes no write lock while a writer commits; snapshots are replaced atomically and never half-written |
| `api/main.py` | every snapshot path answers from JSON without touching `journal.db`, and carries the cache header |

**Not in Phase 5:** `web/` (the dashboard build lands with its own phase; the mount is a
conditional), systemd unit files, and live OANDA trading -- the feed stays behind an
injected transport.

*Update this section before starting any new phase.*

---

### Phase 4 (Strategy & Replay) — COMPLETE
Phase 4 scope, and only Phase 4 scope:

- `config/strategy.yaml`: every tunable the detectors and the silver bullet read, loaded
  through `risk/decimal_yaml.py` so an unquoted threshold is refused at the door. The
  SHA-256 of the parsed document is the run's `strategy_version`, stamped into the
  journal header (`RUN_STARTED`).
- `strategy/detectors.py`: fractal swings (`swing_n` bars either side), liquidity sweeps
  of a prior swing extreme (>= `sweep_min_ticks`, closed back within
  `sweep_close_back_bars`), market structure shifts (close through a swing point, body
  >= `mss_body_k` * ATR(`atr_window`)), and fair value gaps (imbalance >= `fvg_min_atr` *
  ATR, limit entry at the 50% midpoint). All of it on closed mid bars.
- `strategy/silver_bullet.py`: the 03:00-04:00 London and 10:00-11:00 New York windows,
  the reference guard that keeps the London window off the not-yet-concluded London daily
  range, the order lifecycle (`order_expiry_bars`, buffered stop, `rr_target` target), and
  the 16:45 New York dynamic close.
- `strategy/backtest.py`: the replay engine over the Phase 2 execution layer, and the
  proofs that nothing it produced depended on a bar that had not printed.

**Phase 4 outcomes and the invariants that now have machine coverage:**

| Module | Invariant enforced by tests |
| --- | --- |
| `strategy/config.py` | the version is the SHA-256 of the parsed document: key order does not move it, every edited value does |
| `strategy/detectors.py` | a swing is only knowable `swing_n` bars after it prints; a level cannot be swept before it is knowable; streaming indicators match the batch functions at every prefix |
| `strategy/silver_bullet.py` | the windows are New York wall clock across both DST transitions; the London window cannot reference the London daily range; a setup needs sweep, shift and gap inside one 60-minute window; positions are flat by 16:45 |
| `strategy/backtest.py` | tick-streamed indicator calculations match batch computations; a run over `bars[:n]` equals the full run truncated at bar `n` |

`strategy/` joining the tree switches the Phase 0 float-free invariant from *skipping*
to *enforcing* for that package, exactly as `risk/` did in Phase 3.

**Not in Phase 4:** the runtime supervisor wiring (nothing in `runtime/` constructs a
`SilverBulletStrategy` or reads `config/strategy.yaml` yet), risk-based quantity sizing
injected into the replay, `publisher/`, the web dashboard, the FastAPI service, and
systemd units.

*Update this section before starting any new phase.*

---

### Phase 3 (Risk, Metrics & Reporting) — COMPLETE

Phase 3 scope, and only Phase 3 scope:

- `risk/`: `config.py` + `decimal_yaml.py` (Decimal-only config loading), `sizing.py`
  (fixed-fractional position sizing), `drawdown.py` (per-trading-day realized and
  unrealized PnL anchored to 17:00 America/New_York), and `killswitch.py` (latching
  daily-loss kill-switch whose state is persisted as journal events).
- `metrics/`: `performance.py` — the single module where floats are permitted, because
  it is the reporting boundary and produces no number any other module consumes.

| Module | Invariant enforced by tests |
| --- | --- |
| `risk/decimal_yaml.py` | a float in a config file is refused at load time, never coerced |
| `risk/sizing.py` | units floor to `trade_units_precision`; a below-minimum order is refused; realised loss never exceeds the risked amount |
| `risk/drawdown.py` | the trading day is anchored to 17:00 New York wall clock and stays correct across DST transitions |
| `risk/killswitch.py` | tripping latches; the latch survives a process restart through journal events; resting limits are cancelled and new entries blocked until the next day's rollover |
| `metrics/performance.py` | bootstrap CI is byte-reproducible for a fixed seed; `N < 30` returns `INSUFFICIENT_DATA` with no bounds |

`risk/` joining the tree switched the Phase 0 float-free invariant from *skipping* to
*enforcing* for that package. `metrics/` was deliberately **not** added to that set
(AGENTS.md 2.1 permits floats at the display/serialization boundary, and a report is
that boundary).

---

### Phase 2 (Domain, Journal & Execution) — COMPLETE

Phase 2 scope, and only Phase 2 scope:

- `domain/`: immutable `events.py`, `orders.py`, `positions.py`, and the double-entry
  `ledger.py`, all Decimal-only. A float is rejected at every boundary.
- `journal/`: the append-only schema, the SHA-256 hash chain with `verify_chain()`, and the
  thread-isolated `writer.py` consuming a bounded `queue.Queue(maxsize=10000)`.
- `execution/`: the deterministic pessimistic `simulator.py`.

| Module | Invariant enforced by tests |
| --- | --- |
| `domain/events.py` | canonical JSON is a pure function; floats rejected; timestamps UTC |
| `domain/orders.py` | every state transition returns a new object; invalid orders refused |
| `domain/positions.py` | scale-out keeps each slice's cost basis; PnL realized exactly once |
| `domain/ledger.py` | trial balance is identically zero (property-tested over arbitrary histories) |
| `journal/schema.py` | hash chain detects any tampering; UPDATE/DELETE triggers fire |
| `journal/writer.py` | `put_nowait` only; SQLite never runs on the event loop; group commits |
| `execution/simulator.py` | buys on ask, stops trigger on a touch, intra-bar ambiguity resolves against the trade |

---

## 5. Toolchain & Commands

```bash
# Install / sync the environment (always through uv)
uv sync

# Run the full test suite — the only accepted proof of completion
uv run pytest

# Narrow to the phase's suite
uv run pytest tests/path/to/phase_tests.py -v

# Lint (configured in Phase 0)
uv run ruff check .
# Typechecking: mypy is not yet a dependency. Add it and re-enable this line when
# the phase that specifies it starts. docs/ASSUMPTIONS.md records the decision.
# uv run mypy src
```

- Python is managed exclusively by `uv`. Never use `pip`, `python -m venv`, or system Python.
- Never commit a working virtualenv, `.venv/`, or `uv.lock` drift without running `uv sync`.
- Add dependencies with `uv add` (runtime) / `uv add --dev` (test/lint) so the lockfile stays canonical.

---

## 6. Testing Standards

- Tests live under `engine/tests/`, mirroring `engine/src/engine/` structure.
- Every invariant in Section 2 needs explicit test coverage, including:
  - Float-rejection tests (assert no `float` enters domain computations).
  - A test proving no SQLite commit occurs on the event loop thread.
  - Hash-chain tamper-detection tests.
  - Pessimistic fill-model tests, including the "entry filled then immediately stopped" case.
  - Timezone/DST boundary tests using pinned `tzdata`.
- Use `pytest` with deterministic seeds and frozen clocks; no live network calls in unit tests.
- Fault injection tests are first-class: kill the journal worker mid-write, drop the socket, etc.
- Async code is tested with `pytest-asyncio`; never rely on real `sleep()`-based timing races.

---

## 7. Definition of Done (per phase)

A task or phase is complete only when all of the following hold:

1. Only current-phase code exists — no future-phase stubs, TODOs, or speculative imports.
2. `uv run ruff check .` is clean. (mypy is not a dependency yet — see docs/ASSUMPTIONS.md, A7.)
3. `uv run pytest` passes with zero failures; output is shown in your report.
4. Every Section 2 invariant touched by the phase has direct test coverage.
5. No `float` appears in any domain calculation path.
6. Changes are minimal, focused, and consistent with existing naming/structure; no unrelated churn.

---

## 8. Commit & Handoff Discipline

- Keep commits scoped to the active phase; one logical change per commit.
- When handing off, state: files changed, commands run, test results, and any invariants that are
  only partially satisfied.
- Never claim Phase 0 (or any phase) is complete without having executed its test suite.

---

## 9. Agent Checklist

- [ ] Read this file in full.
- [ ] Confirmed `CURRENT ACTIVE PHASE` and stayed within its scope.
- [ ] No future-phase files, stubs, or imports created.
- [ ] Ran `uv run pytest` — results attached.
- [ ] Invariants in Section 2 respected.
- [ ] Lint and typecheck clean.
