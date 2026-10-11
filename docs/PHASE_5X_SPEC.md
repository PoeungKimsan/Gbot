# Phase 5.x — Publisher Fields the Dashboard Needs

Phase 6 builds the `web/` dashboard. It is scoped to `web/` and nothing else
(AGENTS.md §1), so **this document is the whole of the engine-side work Phase 6
needs from someone else.** It is written to be executable: every row names the field,
its type, the endpoint that carries it, and whether the journal already holds it.

Phase 6 was **not** allowed to write these fields itself. Until they land, the dashboard
renders a `NOT PUBLISHED BY THIS ENGINE BUILD` sentinel in the exact cell that is empty
(`web/src/components/NotPublished.tsx`). That is deliberate: a dashboard that quietly
shows a blank cell for a stop-loss is a dashboard nobody can audit.

---

## 0. Conventions

The engine's AGENTS.md §2.1 rules apply at the snapshot boundary, so the dashboard's
contract encodes money and prices the way the journal already does:

| Quantity | Encoding | Reason |
| --- | --- | --- |
| **Money** (PnL, equity, fees, cost, slippage) | **decimal string**, e.g. `"3.40"`, `"-1180.00"` | Exactly what `state.json.equity` and `trades.json.pnl` already are. A float would round-trip through IEEE-754 and disagree with the journal. |
| **Prices** (entry, stop, target, high, low, open, close, sweep level) | **integer ticks**, e.g. `200975` for `$2009.75` | A price is a tick count, and `engine.market.instrument` already prices everything that way. Integer ticks survive JSON with no precision loss and no locale. |
| **Tick size** | decimal string, e.g. `"0.01"` | Published once per payload so ticks can be rendered as prices. |
| **Timestamps** | UTC ISO-8601 with offset, e.g. `"2026-03-09T13:35:00+00:00"` | Matches `state.json.generated_at` and `DomainEvent.occurred_at`. |
| **Booleans** | real booleans, never `0`/`1`/`"true"` | |
| **Absent data** | `null`, never a missing key | A missing key and a null value are different states, and only one of them is distinguishable in TypeScript. |

Every field below is **additive**. Adding a key to a snapshot cannot break any existing
reader: `engine/publisher/outbox.py` writes whole documents atomically, and
`api/main.py` serves them verbatim.

`FXAUUSD`'s tick size is `0.01`. Nothing in this spec hardcodes it: `tick_size` is a
field, so a venue change is a snapshot change and not a dashboard change.

---

## 1. Engine state — `/api/status` (`state.json`)

The status header must show one of `ACTIVE`, `PAUSED`, `NEWS BLACKOUT`, `STALE FEED`.
Today no such field exists, so the header shows `UNKNOWN`.

| Field | Type | Endpoint | Journal status |
| --- | --- | --- | --- |
| `state` | string enum: `ACTIVE` \| `PAUSED` \| `NEWS_BLACKOUT` \| `STALE_FEED` \| `FLAT` | `/api/status` | **Already journaled, needs a fold.** `feed_state_changed` is written on PAUSE/RESUME, on feed failure and on feed end. Its values are the supervisor's vocabulary — `RUNNING`, `PAUSED`, `FAILED`, `STOPPED` — which is not the dashboard's. Map at publish time; do not rename the event (AGENTS.md: a wire rename is a schema change). |
| `state_detail` | string \| null | `/api/status` | **Already journaled.** `feed_state_changed.reason`. |
| `blocked_reason` | string | `/api/status` | **Not journaled.** The supervisor keeps `_blocked_reason` in memory and exposes it only on the control plane's `STATUS`. Either journal it on change or fold it into `equity_snapshot`. |
| `kill_switch` | object \| null: `{ tripped: boolean, day: string (YYYY-MM-DD, New York), loss_r: string, reason: string }` | `/api/status` | **Already journaled.** `risk_killswitch` is written on every transition (`engine/risk/killswitch.py`), with state `ARMED`/`TRIPPED`. |
| `open_positions` / `resting_orders` | integer | `/api/status` | **Already published.** No work. |
| `strategy_version` | string (64 hex chars) | `/api/status` | **Already published.** SHA-256 of the canonical parsed `strategy.yaml`. No work. |
| `generated_at` | UTC ISO-8601 | `/api/status` | **Already published.** The `DATA STALE` banner keys off it (>30 s). No work. |

### The vocabulary mapping

`NEWS_BLACKOUT` has **no journal representation at all** today. `_refresh_block` computes
it in memory from `engine.news.calendar` + `engine.news.breaker` and writes nothing.
`FEED_STATE_CHANGED` is the natural home; add `{"state": "NEWS_BLACKOUT"}` when the
breaker's decision carries `BreakerReason.BLACKOUT`, and the inverse on clear.

`FLAT` (positions flat, orders resting) is not journalled either and is derivable from
`open_positions`; treat it as optional.

---

## 2. Trade tape — `/api/trades` (`trades.json`)

The tape must show **Entry, Stop Loss, Take Profit, Slippage, Realized R**. Entry (`cost`),
Realized R (`r_multiple`) and exit reason are already published. The middle three are not.

`_close_trade` in `engine/publisher/outbox.py` is a **per-row map**: it reads one
`position_closed` row and emits one snapshot row. Stop, target and slippage live on
*different rows* — `order_submitted` (which carries `stop` and `target` as strings) and
`fill_reported`/`slippage_realized` (which carry `slippage_ticks` and `amount`). Publishing
them requires a **correlation step** keyed on `order_id` ↔ `position_id`, which the fold
does not currently do.

| Field | Type | Endpoint | Journal status |
| --- | --- | --- | --- |
| `stop_loss` | integer ticks | `/api/trades` (per trade) | **Already journaled.** `order_submitted.stop`, written by `SilverBulletStrategy`'s `PlaceLimit`. Decimal string today; convert to ticks. |
| `take_profit` | integer ticks | `/api/trades` (per trade) | **Already journaled.** `order_submitted.target`. |
| `slippage` | decimal string (money) | `/api/trades` (per trade) | **Already journaled.** `slippage_realized.amount`. |
| `slippage_ticks` | integer | `/api/trades` (per trade) | **Already journaled.** `fill_reported.slippage_ticks`. |
| `entry_price` | integer ticks | `/api/trades` (per trade) | **Already journaled, already published.** `position_closed.cost` is the fill price, and the dashboard reads it today under the wire name `cost` as a decimal string. Only add a tick-count encoding if `cost` is also being retired; do not add a second name for the same value. |
| `exit_price` | integer ticks | `/api/trades` (per trade) | **Already journaled, already published** as `position_closed.price`, a decimal string. Same note. |
| `filled_at` | UTC ISO-8601 | `/api/trades` (per trade) | **Already journaled** as the event's `occurred_at`; the snapshot drops it. |
| `placed_at` | UTC ISO-8601 | `/api/trades` (per trade) | **Already journaled.** `order_submitted.occurred_at`. |
| `window` | string: `LONDON` \| `NY_AM` | `/api/trades` (per trade) | **Not journaled.** `SilverBulletStrategy` knows it (`PlaceLimit`/`_Position` carry `window`); nothing writes it. |
| `order_id` | string | `/api/trades` (per trade) | **Already journaled.** `order_submitted.order_id`. Needed as the correlation key, and useful for drill-down. |

### Correlation rules the fold must honour

1. Join on `order_id` == `position_id`. `POSITION_OPENED.position_id` is literally set to
   `fill.order_id` (`supervisor._observe`), so the key exists.
2. A closed trade with **no** `order_submitted` row is a **bug**, not a nullable case:
   every position was opened by an order. Emit `null` and log, do not synthesize.
3. A `KILL_FLATTEN` close has no matching order row when the position predates the
   journal. Then `stop_loss`/`take_profit` must be `null`, and the dashboard will show the
   sentinel for that row only.
4. Keep the existing keys (`pnl`, `price`, `cost`, `r_multiple`, `exit_reason`,
   `reference`) byte-identical. This is additive.

---

## 3. Instrument geometry — `/api/status` (`state.json`)

Ticks are unreadable without a tick size, so ticks and geometry must ship together.

| Field | Type | Endpoint | Journal status |
| --- | --- | --- | --- |
| `instrument.name` | string, e.g. `"XAU_USD"` | `/api/status` | **Not journaled.** `EventType.INSTRUMENT_LOADED` is declared in `domain/events.py` but the supervisor never emits it. Either start emitting it, or read `config/instrument.cache.yaml` at publish time — the latter is cheaper and the cache is already the source of truth. |
| `instrument.tick_size` | decimal string, e.g. `"0.01"` | `/api/status` | as above |
| `instrument.pip_size` | decimal string | `/api/status` | as above |
| `instrument.trade_units_precision` | integer | `/api/status` | as above; needed to render quantities. |
| `tick` | integer (the smallest tradeable increment, in ticks — i.e. `1`) | `/api/status` | **Engine-side.** Publish it so the dashboard's tick maths matches `execution/simulator.py`'s "trade through by at least 1 tick" rule. |

---

## 4. Setup visualizer — `/api/setups` (new endpoint)

The visualizer needs **closed M5 bars** plus the detector outputs for the current window:
liquidity sweep levels, MSS markers, FVG boxes. None of it is published. Bars are
aggregated in memory by `BarBuilder`/`BarAggregator` and dropped; the detectors are pure
functions over them (`strategy/detectors.py`) and their results are discarded once an
order is placed.

**This requires a new endpoint.** `api/main.py` currently serves exactly three snapshots
through a `SNAPSHOT_ROUTES` map; add `"setups": "setups.json"` and a matching
`SNAPSHOT_FILES` entry so `atomic_write_json` and `/api/health` pick it up unchanged.

| Field | Type | Endpoint | Journal status |
| --- | --- | --- | --- |
| `generated_at` | UTC ISO-8601 | `/api/setups` | computed at publish time |
| `bars` | array of `{ t: UTC ISO-8601, o: int ticks, h: int ticks, l: int ticks, c: int ticks, v: integer }` | `/api/setups` | **Must start journaling.** The publisher has no bar rows. Cheapest path: journal an `M5_BAR` event per closed bar (a new `EventType`), or have the supervisor keep a bounded ring of the last N bars and write it into the snapshot. Either way, cap it — the dashboard needs one window, not a year. |
| `sweep` | object \| null: `{ direction: "LONG" \| "SHORT", level_ticks: int, breach_ticks: int, close_back_ticks: int, confirmed_at: UTC ISO-8601 }` | `/api/setups` | **Must start journaling.** `LiquiditySweep` exists (`strategy/detectors.py`) but nothing records it. |
| `mss` | object \| null: `{ direction: "LONG" \| "SHORT", broken_swing_ticks: int, close_ticks: int, body_ticks: int, atr_ticks: int, confirmed_at: UTC ISO-8601 }` | `/api/setups` | **Must start journaling.** `MarketStructureShift`. |
| `fvg` | object \| null: `{ direction: "LONG" \| "SHORT", top_ticks: int, bottom_ticks: int, midpoint_ticks: int, size_ticks: int, confirmed_at: UTC ISO-8601 }` | `/api/setups` | **Must start journaling.** `FairValueGap`. |
| `order` | object \| null: `{ order_id, entry_ticks: int, stop_ticks: int, target_ticks: int, expires_at: UTC ISO-8601 }` | `/api/setups` | **Already journaled** as `order_submitted`; the visualizer wants it joined onto the setup. |
| `levels` | array of `{ kind: "ASIAN_RANGE" \| "LONDON_RANGE" \| "PREVIOUS_DAY" \| "SWING_HIGH" \| "SWING_LOW", ticks: int \| null, from_utc: UTC ISO-8601, to_utc: UTC ISO-8601 }` | `/api/setups` | **Must start journaling.** `ALLOWED_REFERENCES` in `strategy/silver_bullet.py` is the policy; the ranges themselves are computed and dropped. The *policy* matters to the chart: a level the window is not allowed to reference must not be drawn, or the chart will show the engine reading the future. |

### The no-repaint invariant must survive the round trip

`strategy/detectors.py` distinguishes `index` from `confirmed_index` deliberately — a swing
is only knowable `swing_n` bars after it prints. The snapshot must publish **only
confirmed** geometry (`confirmed_at`/`confirmed_index`), or the dashboard becomes the one
place that leaks the future. Phase 4 proved this on the replay path; publishing
unconfirmed geometry would quietly undo it in the UI.

---

## 5. Economic calendar radar — `/api/calendar` (new endpoint)

The radar needs Tier-1 events with countdowns and the blackout gates around them. The
calendar lives entirely in `engine.news.calendar` in memory and the publisher never sees
it. New endpoint, same three-snapshot pattern.

| Field | Type | Endpoint | Journal status |
| --- | --- | --- | --- |
| `generated_at` | UTC ISO-8601 | `/api/calendar` | computed at publish time |
| `events` | array of `{ name: string, impact: "high" \| "medium" \| "low", kind: "RELEASE" \| "ADVISORY", timestamp_utc: UTC ISO-8601 \| null, local_date: string (YYYY-MM-DD, New York), blackout_before_seconds: int, blackout_after_seconds: int }` | `/api/calendar` | **Must start journaling**, or read `config/schedule.yaml` at publish time. FRED answers *which day*, the YAML answers *what time* (`engine.news.calendar` docstring). The publisher has neither. |
| `blackouts` | array of `{ name: string, start_utc: UTC ISO-8601, end_utc: UTC ISO-8601, blocking: boolean }` | `/api/calendar` | **Must start journaling.** `BlackoutWindow` is computed and dropped. |
| `next_event` | object \| null (the earliest future `RELEASE` event) | `/api/calendar` | derived server-side; saves the client a sort. |

### Tiering

"Tier-1" is currently the config word `impact: high` plus
`EventKind.RELEASE` (`EconomicEvent.is_blocking`). Publish the flag rather than making the
dashboard re-derive it: an advisory hit must never be able to gate anything, and the
dashboard must not be the component that decides which is which.

### The clock

Countdowns are computed against the **browser's** clock, which is fine — but a wrong client
clock then shows a wrong countdown. `generated_at` on the payload lets the dashboard show
the skew. Publish it.

---

## 6. Summary

| Endpoint | Fields missing today | Already journaled | Must start journaling |
| --- | --- | --- | --- |
| `/api/status` | `state`, `state_detail`, `blocked_reason`, `kill_switch`, `instrument.*`, `tick` | `state`→`feed_state_changed`, `state_detail`→`reason`, `kill_switch`→`risk_killswitch` | `blocked_reason`, `instrument.*` |
| `/api/trades` | `stop_loss`, `take_profit`, `slippage`, `slippage_ticks`, `entry_price`, `exit_price`, `filled_at`, `placed_at`, `window`, `order_id` | all of the above | `window` (and the correlation step in `_close_trade`) |
| `/api/setups` | everything | — | bars, `sweep`, `mss`, `fvg`, `levels`, `order` |
| `/api/calendar` | everything | — | `events`, `blackouts`, `next_event` |

**Blocking for a usable dashboard:** §1 `state`, §2 `stop_loss`/`take_profit`/`slippage`.
**Blocking for the headline views:** §4 (whole endpoint), §5 (whole endpoint).

---

## 7. How Phase 6 consumes this now

`web/src/api/contract.ts` is the typed mirror of this document. `web/src/api/client.ts`
normalises each snapshot into that contract and maps every missing field to
`PublicationStatus.MISSING`. `NotPublished.tsx` renders the sentinel. When the fields land,
the sentinel disappears with **no dashboard change** — the normalizers already read them.

Fixtures in `web/src/api/fixtures.ts` are built to the *full* contract, including the
Phase 5.x fields, so every view is exercised with real data rather than only with holes.
The fixtures are labelled as fixtures and are never served in a production build.
