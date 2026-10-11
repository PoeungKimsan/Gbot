# AGENTS.md

This file is the authoritative operating guide for any AI agent or human contributor working on
this repository. Read it fully before writing code. When this file conflicts with a request,
the invariants in Section 2 win — raise the conflict instead of silently violating them.

---

## 0. Project Summary

An automated **XAUUSD (gold) trading bot** that trades **live on MetaTrader 5** and shows
real-time state on a web dashboard.

- **Engine (`engine/`)** — pulls real-time quotes and bars from the MT5 terminal, builds M1/M5
  bars, evaluates the ICT **Asian Judas Swing** strategy on closed M5 bars, and places market
  orders with attached stop-loss and take-profit through MT5.
- **API (`api/main.py`)** — FastAPI on `127.0.0.1:8080`. Starts the live trader on launch,
  streams ticks / bars / positions / signals over the `/ws/live` WebSocket, serves REST snapshots
  under `/api/*`, and mounts `web/dist` when it exists.
- **Dashboard (`web/`)** — React + Vite + Tailwind, styled per `DESIGN-supabase.md`.
- **Safety controls (`risk/`)** — Decimal position sizing, per-trading-day drawdown and a latching
  daily-loss kill-switch whose state is persisted to a hash-chained SQLite journal.

> **History:** the repository previously contained an OANDA-based paper-trading research lab
> (pessimistic fill simulator, Silver Bullet backtester, publisher, systemd units, control plane,
> fault-injection drills). That system was **removed on purpose**. Do **not** recreate any of it
> unless the user explicitly asks.

---

## 1. Operating Rules

1. **Stay in scope.** Only change what the request needs. Do not scaffold stub files, placeholder
   classes, `TODO` skeletons or speculative modules for features nobody asked for.
2. **Real money is at stake.** Anything touching order placement, sizing or the kill-switch must
   be tested and called out explicitly in your report. Never weaken a safety control to make a
   test pass or an order go through.
3. **Verify before claiming done.** Run `uv run pytest` (and `npm run build` for `web/` changes)
   and report the actual output. Never claim a suite passed without running it.
4. **No hallucinated simplifications.** Do not swap `Decimal` for `float`, move SQLite writes onto
   the event loop, or remove SL/TP from orders for convenience.

---

## 2. Mandatory Technical Invariants

Non-negotiable. Violating any of them is a defect, even if tests still pass.

### 2.1 Zero Floating-Point Arithmetic
- Use `Decimal` or integer ticks exclusively for currency, balances, lot sizes, prices and PnL.
- `float` is banned in `domain/`, `execution/`, `feed/`, `market/`, `risk/`, `strategy/` and
  `journal/` (enforced by `engine/tests/test_invariants.py`).
- MT5's Python API returns and accepts floats: convert to `Decimal` via `Decimal(str(x))` the
  moment a value arrives, and convert back to `float` in **one** named boundary helper used to
  build `mt5.order_send` requests. That helper is the only allowed exemption and must be listed
  explicitly in `test_invariants.py` (not done yet; see Section 4). Floats for chart JSON in
  `runtime/live_trader.py` are the display boundary and are allowed.
- Round explicitly (`ROUND_HALF_EVEN` unless the venue mandates otherwise) through the helpers in
  `risk/rounding.py` rather than scattered `.quantize()` calls.

### 2.2 Orders Always Carry Protection
- Every live order is sent with a stop-loss and a take-profit. An order without both must be
  refused, not sent (the broker still accepts `None` today; see Section 4).
- Lot size comes from configuration or `risk/sizing.py`, never from a hard-coded guess inside
  strategy code.

### 2.3 Kill-Switch & Journal
- The kill-switch latches on the daily loss limit, blocks new entries until the next trading-day
  rollover, and must survive a process restart by being restored from journal events.
- All SQLite writes run on the dedicated journal worker **OS thread** fed by a bounded
  `queue.Queue`; the asyncio loop only calls `put_nowait`. Never commit SQLite on the event loop.
- `PRAGMA journal_mode = WAL;` and `PRAGMA synchronous = FULL;`.
- Journal rows are SHA-256 hash-chained (each row commits to the previous row's hash plus its
  canonical payload). Migrations are forward-only and idempotent.

### 2.4 Timezone Handling
- Store timestamps as UTC. Resolve sessions (Asian range, London/New York windows, the 17:00
  New York trading-day rollover) via `zoneinfo.ZoneInfo("America/New_York")`.
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
│   └── risk.yaml            # Decimal-only risk limits (unquoted floats are refused)
├── engine/
│   ├── src/engine/
│   │   ├── domain/          # Immutable events, orders, positions, ledger (Decimal)
│   │   ├── execution/       # mt5_broker.py — order placement, open positions
│   │   ├── feed/            # mt5_client.py — MT5 connection, quotes, historical bars
│   │   ├── journal/         # Thread-isolated SQLite WAL writer + hash chain
│   │   ├── market/          # Quote/Bar models, bar builder/aggregator, instrument spec
│   │   ├── risk/            # sizing, drawdown, killswitch, rounding, decimal_yaml
│   │   ├── runtime/         # live_trader.py — orchestrates feed → strategy → broker → WS
│   │   └── strategy/        # judas_swing.py — Asian range Judas Swing setup
│   └── tests/               # Mirrors src/; plus test_invariants.py (static rule scans)
├── web/                     # React + Vite + Tailwind dashboard
├── DESIGN-supabase.md       # Dashboard design system
└── pyproject.toml           # uv-managed toolchain
```

Dependencies flow inward toward `domain/`. `strategy/` is a pure function of the bars it is given:
it never talks to the broker, the journal or the clock. Only `runtime/` wires layers together.

---

## 4. Current State

**Working & Verified:**
- Live MT5 feed → M5 bars → Judas Swing signals → MT5 market orders with mandatory SL/TP.
- WebSocket + REST API with sub-50ms tick streaming and live Supabase-styled dashboard with live NY algorithmic clock.
- **Dynamic Position Sizing:** `live_trader.py` calculates lot sizes dynamically via `calculate_lot_size` using account equity and stop-loss distance (`risk/sizing.py`), enforcing the 1% risk limit. Orders with stops too wide for the minimum lot size are refused.
- **Deal History Reconciliation:** Position closes on MT5 are reconciled against actual closing deals (`get_closed_deal_for_position`), recording audited exit prices, broker commissions/swaps, and exact realized PnL into the kill-switch and `_completed_trades`.
- **Judas Swing Strategy Purity:** Reclaims strictly guard against overshooting the opposite session boundary (`entry >= asian_low` for SELL, `entry <= asian_high` for BUY), sweeps are cleanly reset at session open (18:00 NY), and 300 M5 seed bars guarantee full 24-hour Asian session lock.
- **Safety controls wired:** `live_trader.py` enforces `risk/killswitch.py` and `risk/drawdown.py`. Orders are intercepted and refused when the daily loss limit (-3R) is reached.
- **Journal persistence:** Kill-switch state is persisted to `journal.db` via the background OS thread worker, and cleanly restored across process restarts.
- **Protected orders:** `MT5Broker` strictly enforces positive Stop-Loss and Take-Profit with geometry validation. Unprotected orders are refused.
- **Zero float purity:** Decimal arithmetic everywhere in domain packages; `_to_mt5_float` in `mt5_broker.py` is the single named boundary helper for MT5 C-API requests.
- **Telegram Notifications:** Asynchronous, non-blocking real-time alert dispatch (`engine/runtime/telegram.py`) for order placement, deal-audited closures, and kill-switch latching.
- **Clean toolchain & tests:** `uv run pytest` passes (532 tests), `uv run ruff check .` reports 0 errors, and `npm run build` succeeds. Full coverage for API, LiveTrader, MT5Broker, MT5Feed, and TelegramNotifier.

**Known gaps:**
*None at this time.*

*Update this section whenever a gap is closed or a new one is found.*

---

## 5. Toolchain & Commands

```bash
uv sync                                                  # install / sync Python env
uv run pytest                                            # full test suite
uv run ruff check .                                      # lint
uv run uvicorn api.main:app --host 127.0.0.1 --port 8080 # run engine + API (needs MT5 terminal)

cd web && npm ci && npm run dev                          # dashboard dev server on :5173
cd web && npm run build                                  # typecheck + build to web/dist
```

- Python is managed exclusively by `uv`. Never use `pip`, `python -m venv` or system Python.
- Add dependencies with `uv add` / `uv add --dev` so `uv.lock` stays canonical.
- The `MetaTrader5` package is Windows-only and needs a running, logged-in MT5 terminal.

---

## 6. Testing Standards

- Tests live under `engine/tests/`, mirroring `engine/src/engine/`.
- **Unit tests never touch a real MT5 terminal or the network.** Inject fakes for the MT5 module
  and the broker; the autouse fixture in `conftest.py` refuses non-loopback DNS.
- Required coverage: float rejection, kill-switch latch + restart restore, hash-chain tamper
  detection, no SQLite commit on the event loop, DST boundaries with pinned `tzdata`, and that an
  order without SL/TP is refused.
- Use deterministic seeds and frozen clocks; test async code with `pytest-asyncio`, never with
  real `sleep()` timing races.

---

## 7. Definition of Done

1. `uv run pytest` passes (or any pre-existing failure from Section 4 is unchanged and named).
2. `uv run ruff check .` reports no new errors in files you touched.
3. `npm run build` passes if `web/` changed.
4. Every Section 2 invariant you touched has a direct test.
5. Changes are minimal and focused; Section 4 is updated if a gap was closed or found.

---

## 8. Handoff

State: files changed, commands run, test results, and any invariant only partially satisfied.
Flag anything that changes live order behaviour in its own line.
