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

> **CURRENT ACTIVE PHASE: Phase 0 (Workspace Provisioning & Gates)**

Phase 0 scope, and only Phase 0 scope:
- Repository skeleton per Section 3 (directories only, where needed by tooling).
- `pyproject.toml` with the `uv`-managed toolchain, pinned dependencies (including `tzdata`),
  and configured test/lint gates.
- Test infrastructure: `uv run pytest` runs green on an empty or minimal suite.
- Any actual engine modules belong to later phases. Do not write them yet.

**Phases 1+ (NOT started — do not implement):** feed, market data & session math, news calendar,
domain events & Decimal ledger, execution/fill model, risk limits, ICT strategy detectors,
SQLite journal worker + hash chain, publisher/projections, runtime supervisor + IPC, web
dashboard, FastAPI service, systemd units.

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
