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

> **CURRENT ACTIVE PHASE: Phase 8 (Fault Injection & Soak Validation)**

Phase 8 scope, and only Phase 8 scope:

- `engine/tests/test_fault_injection.py`: six recovery drills, each one an operator story
  with a machine-checkable ending. A harness child process runs a real supervisor over a
  real journal and is `SIGKILL`ed, after which the parent verifies the hash chain over
  whatever committed and restores both the open position and the latched kill-switch from
  journal events alone. The same harness, sent `SIGTERM` instead, must drain the queue
  inside the ten-second budget. A 500 ms stall injected into the writer thread's `commit`
  must not stall the event loop — measured as the wall-clock span between the first and
  last quote the loop accepted. Quote silence past `STALE_AFTER` forbids entry emission,
  asserted differentially against the full stream. An HTTP 429 on the advisory scrape is a
  warning, not a halt. A genuinely saturated bounded queue exits 70, and the engine unit's
  `RestartPreventExitStatus` is read to prove systemd will not restart into it.
- `scripts/soak_report.py`: event counts by type, write-latency distribution from the row's
  own two timestamps, WAL dimensions, `verify_chain()` over the whole journal, and bar
  continuity from the `bars` counter. Opens the journal `mode=ro`, so a report can be
  produced while the engine is committing.
- `engine/tests/deploy/test_soak_report.py`: machine coverage for every summary.

**Phase 8 outcomes and the invariants that now have machine coverage:**

| Module | Invariant enforced by tests |
| --- | --- |
| `engine/tests/test_fault_injection.py` | a `SIGKILL`ed child leaves a valid hash chain and journal events sufficient to restore the open position and the latched kill-switch; `SIGTERM` and the control-plane `SHUTDOWN` both reach the same drain, with the queue empty and committed == rows, inside the budget |
| `engine/tests/test_fault_injection.py` | a 500 ms commit stall backs the *queue* up, not the event loop: the quote-delivery span stays under a second while the writer falls behind, and every event still lands with a valid chain |
| `engine/tests/test_fault_injection.py` | quote silence past `STALE_AFTER` is reported stale at eleven seconds and not at nine, a live heartbeat contradicts quote silence, and a truncated feed emits a strict prefix of the full one's orders |
| `engine/tests/test_fault_injection.py` | a throttled advisory source logs a warning and the FRED-confirmed events survive; it never creates a blackout |
| `engine/tests/test_fault_injection.py` | a real bounded queue really overflows through `put_nowait`, `JournalExhausted` escapes, and the run returns 70 — and `xauusd-engine.service` names 70 in `RestartPreventExitStatus` |
| `scripts/soak_report.py` | a broken chain is reported as broken rather than raised; an unparseable `created_at` is skipped rather than counted as zero latency; bar gaps and regressions are surfaced, not averaged away |

**Not in Phase 8:** a live soak run. Soak validation is confirmed manually on the deployed
host; this phase ships the drills and the report that make the confirmation meaningful.

*Update this section before starting any new phase.*

---

### Phase 7 (Deployment & Operations) — COMPLETE
Phase 7 scope, and only Phase 7 scope:

- `systemd/`: four units and one timer. `xauusd-engine` (Type=notify, `xauusd-engine` /
  `xauusd-journal`, UMask 0007, RestartPreventExitStatus 70 78, WatchdogSec 60,
  TimeoutStopSec 15, StateDirectory `xauusd/journal` 0770), `xauusd-publisher`
  (`xauusd-pub` / `xauusd-proj` + `xauusd-journal`, UMask 0027, StateDirectory
  `xauusd/projection` 0750, ReadOnlyPaths journal / ReadWritePaths projection),
  `xauusd-web` (`xauusd-web`, `xauusd-proj`, uvicorn on 127.0.0.1:8080), and
  `xauusd-backup.service` + `.timer` for the nightly fold of `scripts/backup.py`.
- `scripts/backup.py`: `VACUUM INTO` a consistent copy, `PRAGMA integrity_check` it,
  verify the row-level hash chain over the copy, and rotate past 14 days keyed on the
  filename rather than the mtime. A copy that fails either check is deleted.
- `scripts/publish_loop.py`: the publisher's process. Phase 5 shipped a library; a unit
  needs something to exec.
- `scripts/make_release.sh`: a reproducible tarball of the code plus the built `web/dist`,
  with a `.sha256`. `scripts/deploy_ubuntu.sh`: extract, `uv sync --frozen --no-dev` into
  `/opt/xauusd/python`, run the gates, repoint `/opt/xauusd/app`, reload and restart.
- `docs/DEPLOYMENT.md`: the host contract, the account/group layout, the release and
  rollback procedure, and the three things the shipped engine refuses to do that the
  deployment currently works around rather than fixes.

**Phase 7 outcomes and the invariants that now have machine coverage:**

| Module | Invariant enforced by tests |
| --- | --- |
| `engine/tests/deploy/test_systemd_units.py` | every directive the brief specified is present; the web unit is **not** in `xauusd-journal`; only the engine, the publisher and the backup touch the journal; every unit is `ProtectSystem=strict` and `NoNewPrivileges=yes` |
| `scripts/backup.py` | a copy failing `integrity_check` or the hash chain is deleted, not retained — including when the failure arrives by a route the validators do not expect; the journal is never opened for writing; rotation is boundary-exact and keyed on the filename, not the mtime |
| `scripts/publish_loop.py` | a missing journal is waited on rather than exited over; a relative projection path is refused; a stop signal arriving *during* a fold still stops the process (the reason `_Stop` is a `BaseException`) |
| `scripts/deploy_ubuntu.sh` | the gates run before the symlink is repointed — asserted against the *commands*, not against the header comment that also mentions `ln -sfn` |
| `.github/workflows/ci.yml` | the `deploy` job creates the three accounts and two groups, then `systemd-analyze verify`s all five units on every push |

**Not in Phase 7:** the live OANDA feed, and any change to `engine/` or `api/`. The engine
still refuses to start without an injected quote source, and the deployment records that
consequence rather than editing the engine to hide it. See `docs/DEPLOYMENT.md` §4.

---

### Phase 6 (Web Dashboard) — COMPLETE
**Phase 6 outcomes and the invariants that now have machine coverage:**

| Module | Invariant enforced by tests |
| --- | --- |
| `web/src/content/disclosure.ts` | the notice is the file on disk byte for byte; a missing or an empty disclosure fails `vite build` with a named error (verified against missing, empty and whitespace-only) |
| `web/src/components/Disclosure.tsx` | the disclosure renders verbatim inside **every** performance panel — the hero panel and the expectancy island, in the full, insufficient-sample and no-metrics variants alike |
| `web/src/components/PerformancePanel.tsx` | `N < 30` prints `INSUFFICIENT DATA (<30 TRADES)` and no bounds, in both panels; at or above 30 it prints the bounds and drops the notice; a payload claiming `COMPLETE` over twelve trades is not believed |
| `web/src/components/StatusHeader.tsx` | the stale banner appears at 31 s and not at 30 s, and follows the component's own clock rather than the render |
| `web/src/components/TradeTape.tsx` | stop loss, take profit and slippage render `NOT PUBLISHED BY THIS ENGINE BUILD` for every row against today's `trades.json`, and never a zero |
| `web/src/api/client.ts` | every request is a relative `/api/*` path — an absolute URL would fail the suite |
| `web/src/api/contract.ts` | a field the payload does not carry is `null`, never a zero, a default or an empty string |
| `web/src/lib/decimal.ts` | `20_097_500` ticks at `$0.01` renders `$200,975.00`, not `$200974.99999999997` |

**Not in Phase 6:** engine or API changes. The missing snapshot fields live in
`docs/PHASE_5X_SPEC.md` and belong to whoever owns `engine/publisher/outbox.py` next.

*Update this section before starting any new phase.*

---

### Phase 5 (Runtime Supervisor, Control Plane & Projections) — COMPLETE
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
