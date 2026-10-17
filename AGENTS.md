# AGENTS.md

This file is the authoritative operating guide for any AI agent or human contributor working on
this repository. Read it fully before writing code. When this file conflicts with a request,
the invariants in Section 2 win — raise the conflict instead of silently violating them.

---

## 0. Project Summary

An automated **Multi-Asset ETF Momentum Rotation Engine (VAR_117)** that trades **live/paper via Alpaca Markets**
and shows real-time portfolio allocation and metrics on a web dashboard.

- **Strategy (`engine/src/engine/strategy/etf_momentum.py`)** — Candidate model `VAR_117` (from 294-variant grid search):
  - **Universe (12 US ETFs):** `SPY`, `QQQ`, `IWM`, `EFA`, `EEM`, `IEF`, `TLT`, `SHY`, `LQD`, `VNQ`, `GLD`, `DBC`.
  - **Cash Proxy:** FRED 3-Month T-Bill (`TB3MS`) / Account Cash Balance.
  - **Momentum Lookback:** Trailing 9 months (189 trading days).
  - **Selection:** Top $K=3$ assets by 9-month return.
  - **Inverse-Volatility Weighting:** 12-month (252 trading days) inverse-volatility weighting computed across selected top-3 assets ($w_i \propto 1/\sigma_i$), normalized so total invested weight $\le 1.0$.
  - **Cash Filter:** Absolute momentum hurdle ($R_{9\text{M}} > 0$). If an asset in the top 3 has $R_{9\text{M}} \le 0$,
    its allocation is redirected to cash (surviving assets do not re-expand).
  - **Execution Timing:** Rebalance signal evaluated at month-end market close ($t$); rebalancing executed at market close
    of the first trading day of the next month ($t+1$, 1-day implementation delay).
- **Execution (`engine/src/engine/execution/alpaca_broker.py`)** — Connects to Alpaca Markets (Paper / Live) via async REST API.
  Calculates delta rebalancing orders against current portfolio holdings, supports fractional shares, and includes an automatic
  in-memory mock paper broker fallback when API keys are not configured.
- **Feed (`engine/src/engine/feed/alpaca_feed.py`)** — Ingests daily ETF historical bars and real-time quotes, seeding 252 days of
  historical data for instantaneous momentum & volatility evaluation.
- **API (`api/main.py`)** — FastAPI on `127.0.0.1:8080`. Starts the live trader on launch, streams portfolio updates / holdings /
  radar rankings / rebalance orders over `/ws/live`, serves REST snapshots under `/api/*`, and mounts `web/dist` when built.
- **Dashboard (`web/`)** — React + Vite + Tailwind, styled per `DESIGN-supabase.md`.
- **Safety controls (`risk/`)** — Decimal portfolio sizing, cash filter hurdle, and a latching portfolio drawdown kill-switch
  whose state is persisted to a hash-chained SQLite WAL journal.

---

## 1. Operating Rules

1. **Stay in scope.** Only change what the request needs. Do not scaffold stub files, placeholder
   classes, `TODO` skeletons or speculative modules for features nobody asked for.
2. **Real money is at stake.** Anything touching order placement, sizing, rebalancing or the kill-switch must
   be tested and called out explicitly in your report. Never weaken a safety control to make a
   test pass or an order go through.
3. **Verify before claiming done.** Run `uv run pytest` (and `npm run build` for `web/` changes)
   and report the actual output. Never claim a suite passed without running it.
4. **No hallucinated simplifications.** Do not swap `Decimal` for `float`, move SQLite writes onto
   the event loop, or bypass the cash filter for convenience.

---

## 2. Mandatory Technical Invariants

Non-negotiable. Violating any of them is a defect, even if tests still pass.

### 2.1 Zero Floating-Point Arithmetic
- Use `Decimal` or integer ticks exclusively for currency, balances, shares, prices, returns, volatilities, and PnL.
- `float` is banned in `domain/`, `execution/`, `feed/`, `market/`, `risk/`, `strategy/` and
  `journal/` (enforced by `engine/tests/test_invariants.py`).
- External APIs (Alpaca / Yahoo) return strings or numbers: convert to `Decimal` via `Decimal(str(x))` the
  moment a value arrives. Any serialization boundary helper must be float-free or explicitly exempt in `test_invariants.py`.
  Floats for chart JSON display boundaries are allowed only in `runtime/live_trader.py` and `api/`.
- Round explicitly (`ROUND_HALF_EVEN` unless the venue mandates otherwise) through the helpers in
  `risk/rounding.py` rather than scattered `.quantize()` calls.

### 2.2 Portfolio-Level Protection & Systematic Risk Control
- All asset allocations strictly obey the `VAR_117` systematic rules:
  1. No asset is held unless it ranks in the top $K=3$ AND passes the Cash Filter ($R_{9\text{M}} > 0$).
  2. Any allocation for failed assets is held as cash.
  3. Total portfolio weight across all assets never exceeds 100% ($\sum w_i \le 1.0$).
  4. The latching drawdown kill-switch blocks new allocation entries and liquidates active positions if
     the maximum portfolio drawdown or daily loss threshold is breached.

### 2.3 Kill-Switch & Journal
- The kill-switch latches on the portfolio drawdown limit, blocks new rebalancing buys until explicit reset,
  and must survive a process restart by being restored from journal events.
- All SQLite writes run on the dedicated journal worker **OS thread** fed by a bounded
  `queue.Queue`; the asyncio loop only calls `put_nowait`. Never commit SQLite on the event loop.
- `PRAGMA journal_mode = WAL;` and `PRAGMA synchronous = FULL;`.
- Journal rows are SHA-256 hash-chained (each row commits to the previous row's hash plus its
  canonical payload). Migrations are forward-only and idempotent.

### 2.4 Timezone Handling
- Store timestamps as UTC. Resolve sessions and month-end algorithmic rebalancing windows via
  `zoneinfo.ZoneInfo("America/New_York")`.
- `tzdata` is a pinned dependency; never rely on the host OS timezone database.

### 2.5 No AF_UNIX
- `AF_UNIX` must not appear in `engine/` or `api/` (Windows is the primary host). Enforced by
  `test_invariants.py`.

---

## 3. Repository Layout

```
Gbot/
├── api/
│   └── main.py              # FastAPI: lifespan starts the trader, /ws/live, /api/*
├── config/
│   ├── alpaca.yaml          # Alpaca credentials & paper trading configuration
│   └── risk.yaml            # Decimal-only risk limits (drawdown limits, cash filter)
├── engine/
│   ├── src/engine/
│   │   ├── domain/          # Immutable events, orders, positions, ledger (Decimal)
│   │   ├── execution/       # alpaca_broker.py — Alpaca API orders, portfolio delta rebalance
│   │   ├── feed/            # alpaca_feed.py — ETF quotes, historical daily bars
│   │   ├── journal/         # Thread-isolated SQLite WAL writer + hash chain
│   │   ├── market/          # Quote/Bar models, bar builder/aggregator, instrument spec
│   │   ├── risk/            # sizing, drawdown, killswitch, rounding, decimal_yaml
│   │   ├── runtime/         # live_trader.py — orchestrates feed → VAR_117 → broker → WS
│   │   └── strategy/        # etf_momentum.py — VAR_117 Momentum Rotation & Cash Filter
│   └── tests/               # Mirrors src/; plus test_invariants.py (static rule scans)
├── web/                     # React + Vite + Tailwind dashboard (Supabase design)
├── DESIGN-supabase.md       # Dashboard design system
└── pyproject.toml           # uv-managed toolchain
```

Dependencies flow inward toward `domain/`. `strategy/` is a pure function of the historical bars and prices
it is given: it never talks to the broker, the journal or the clock. Only `runtime/` wires layers together.

---

## 4. Current State

**Working & Verified:**
- Architecture upgraded to **VAR_117 Multi-Asset ETF Momentum Rotation**:
  - Pure Decimal strategy in `engine/src/engine/strategy/etf_momentum.py` evaluating 12 ETFs with 9M momentum, 12M inverse-volatility weighting, and Cash Filter.
  - Alpaca broker client in `engine/src/engine/execution/alpaca_broker.py` supporting REST paper/live trading and local mock fallback.
  - Real-time ETF quotes and seeded 252-day historical bar cache in `engine/src/engine/feed/alpaca_feed.py`.
  - Portfolio-level risk management wired in `engine/src/engine/runtime/live_trader.py` with hash-chained SQLite WAL persistence.
  - Real-time WebSocket streaming and REST endpoints in `api/main.py`.
  - Supabase-styled web dashboard in `web/` with live portfolio NAV, active holdings, 12-ETF momentum radar, and rebalance timeline.

**Known gaps:**
*None at this time.*

---

## 5. Toolchain & Commands

```bash
uv sync                                                  # install / sync Python env
uv run pytest                                            # full test suite
uv run ruff check .                                      # lint
uv run python -m uvicorn api.main:app --host 127.0.0.1 --port 8080 # run engine + API

cd web && npm ci && npm run dev                          # dashboard dev server on :5173
cd web && npm run build                                  # typecheck + build to web/dist
```

---

## 6. Testing Standards

- Tests live under `engine/tests/`, mirroring `engine/src/engine/`.
- **Unit tests never touch external live broker APIs or the network.** Inject fakes or use mock modes;
  the autouse fixture in `conftest.py` refuses non-loopback DNS.
- Required coverage: float rejection, kill-switch latch + restart restore, hash-chain tamper
  detection, no SQLite commit on the event loop, DST boundaries with pinned `tzdata`, and exact
  Decimal momentum and inverse-volatility calculation.
- Use deterministic seeds and frozen clocks; test async code with `pytest-asyncio`, never with
  real `sleep()` timing races.

---

## 7. Definition of Done

1. `uv run pytest` passes.
2. `uv run ruff check .` reports no new errors.
3. `npm run build` passes.
4. Every Section 2 invariant is strictly maintained.
5. Changes are minimal, verified, and complete.

---

## 8. Handoff

State: files changed, commands run, test results, and any invariant only partially satisfied.
Flag anything that changes live order behaviour in its own line.
