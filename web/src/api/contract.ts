/**The wire contract between the engine's read-only API and this dashboard.

Every type in this file is the *published* shape of a `engine/publisher/outbox.py`
snapshot, plus the Phase 5.x fields specified in `docs/PHASE_5X_SPEC.md`. The two are
distinguished by nullability, and only by nullability:

* a **non-nullable** field is published today;
* a **nullable** field is a Phase 5.x addition this engine build does not emit yet, and
  the dashboard renders `NOT PUBLISHED BY THIS ENGINE BUILD` in its place.

Encodings follow the engine's own rules (`AGENTS.md` 2.1, and the conventions table in
the spec): **money is a decimal string**, **prices are integer ticks**, **timestamps are
UTC ISO-8601**. `engine/metrics/performance.py` is the one module where the engine
permits floats because it is the reporting boundary, so `win_rate`, `profit_factor` and
the bootstrap bounds arrive as JSON numbers and stay numbers.

This file contains no fetch, no state and no side effects. It is the schema, and the
schema is what a Phase 5.x change should be written against.
*/

// --------------------------------------------------------------------------- //
// shared constants
// --------------------------------------------------------------------------- //

/** Mirrors `engine.metrics.performance.MINIMUM_SAMPLE_SIZE`.

Below this trade count the engine reports `INSUFFICIENT_DATA` and emits no confidence
interval, because a bootstrap interval over eight trades is fabricated precision rather
than caution. The dashboard repeats the number rather than trusting the status string
alone: a snapshot that claims `COMPLETE` with four trades is a snapshot that lies.
*/
export const MINIMUM_SAMPLE_SIZE = 30;

/** Mirrors `api/main.py` `CACHE_CONTROL`.

The API caches every snapshot response for ten seconds, so a dashboard that polls faster
than that is polling its own cache.
*/
export const CACHE_MAX_AGE_SECONDS = 10;

/** How old a status payload may be before the dashboard calls it stale. */
export const STALE_AFTER_SECONDS = 30;

/** What a cell says when the engine has not published the field behind it. */
export const NOT_PUBLISHED = "NOT PUBLISHED BY THIS ENGINE BUILD";

/** The state vocabulary the status header displays. */
export type EngineState =
  | "ACTIVE"
  | "PAUSED"
  | "NEWS_BLACKOUT"
  | "STALE_FEED"
  | "FLAT"
  | "UNKNOWN";

/** The side a trade or a structure marker argues for. */
export type Side = "LONG" | "SHORT";

/** Which silver-bullet window a setup belongs to. Mirrors `WindowName`. */
export type WindowName = "LONDON" | "NY_AM";

/** How the publisher classifies a close. Mirrors `execution/simulator.py` `FillKind`. */
export type ExitReason =
  | "TARGET"
  | "STOP_LOSS"
  | "SESSION_CLOSE"
  | "KILL_SWITCH"
  | "CONTROL_PLANE"
  | "ORDER_EXPIRED"
  | "UNKNOWN";

// --------------------------------------------------------------------------- //
// /api/status  ->  state.json
// --------------------------------------------------------------------------- //

/** The venue's price geometry, so tick counts can be rendered as prices. */
export interface Instrument {
  readonly name: string;
  /** Decimal string, e.g. `"0.01"`. */
  readonly tickSize: string;
  /** Decimal string, e.g. `"0.01"`. */
  readonly pipSize: string;
  readonly tradeUnitsPrecision: number;
}

/** The latching daily-loss kill-switch. */
export interface KillSwitch {
  readonly tripped: boolean;
  /** New York trading day, `YYYY-MM-DD`. */
  readonly tradingDay: string;
  /** Realized loss for the day, in R. Decimal string. */
  readonly lossR: string;
  readonly reason: string;
}

/** `state.json`, the run header a status page wants. */
export interface StatusPayload {
  readonly runId: string;
  /** SHA-256 of the canonical parsed `config/strategy.yaml`. */
  readonly strategyVersion: string;
  readonly strategyVersionAlgorithm: string;
  /** Decimal string. */
  readonly equity: string;
  readonly bars: number;
  readonly openPositions: number;
  readonly restingOrders: number;
  /** UTC ISO-8601, with offset. */
  readonly generatedAt: string | null;

  // -- Phase 5.x (see docs/PHASE_5X_SPEC.md §1) ------------------------------ //
  /** `UNKNOWN` until the publisher folds `feed_state_changed`. */
  readonly state: EngineState;
  readonly stateDetail: string | null;
  readonly blockedReason: string | null;
  readonly killSwitch: KillSwitch | null;
  readonly instrument: Instrument | null;
}

// --------------------------------------------------------------------------- //
// /api/trades  ->  trades.json
// --------------------------------------------------------------------------- //

/** One closed trade, joined from the journal's order, fill and close rows. */
export interface TradeRecord {
  readonly tradeId: string;
  readonly side: Side;
  readonly window: WindowName | null;

  /**
   * Decimal string. The publisher writes the *fill* price under `cost` — an odd wire name,
   * but it is `trade.entry_price` verbatim, and `supervisor._record_trade` is where the
   * mapping is made. Read it as the entry price.
   */
  readonly entryPrice: string | null;
  /**
   * Decimal string. The publisher writes the exit price under `price`.
   */
  readonly exitPrice: string | null;
  /**
   * Integer ticks. Phase 5.x: `order_submitted.stop`, correlated onto the close.
   */
  readonly stopLoss: number | null;
  /**
   * Integer ticks. Phase 5.x: `order_submitted.target`, correlated onto the close.
   */
  readonly takeProfit: number | null;

  /** Decimal string. Realized PnL. */
  readonly pnl: string;
  /** Decimal string. Adverse slippage charged on the entry fill. */
  readonly slippage: string | null;
  /** Integer ticks. */
  readonly slippageTicks: number | null;
  /** Decimal string. Realized R multiple. */
  readonly realizedR: string;
  /** Decimal string. */
  readonly quantity: string;

  readonly exitReason: ExitReason;
  readonly reference: string;
  readonly filledAt: string | null;
  readonly placedAt: string | null;
}

/** `trades.json`. */
export interface TradesPayload {
  readonly count: number;
  readonly trades: readonly TradeRecord[];
}

// --------------------------------------------------------------------------- //
// /api/metrics  ->  metrics.json
// --------------------------------------------------------------------------- //

/** A bootstrap interval around the expectancy in R. */
export interface ConfidenceInterval {
  readonly lower: number;
  readonly upper: number;
}

/** `metrics.json`, the Phase 3 reporting module's output verbatim. */
export interface MetricsPayload {
  readonly status: "COMPLETE" | "INSUFFICIENT_DATA";
  readonly tradeCount: number;
  /** Numbers: the engine's float-permitted reporting boundary. */
  readonly realizedPnl: number;
  readonly maxDrawdown: number;
  readonly winRate: number | null;
  readonly profitFactor: number | null;
  readonly expectancyR: number | null;
  readonly confidenceInterval: ConfidenceInterval | null;
}

// --------------------------------------------------------------------------- //
// /api/health
// --------------------------------------------------------------------------- //

/** `/api/health`, which is live rather than snapshotted. */
export interface HealthPayload {
  readonly status: string;
  /** Which of the three snapshot documents exist right now. */
  readonly snapshots: readonly string[];
}

// --------------------------------------------------------------------------- //
// /api/setups  ->  setups.json  (docs/PHASE_5X_SPEC.md §4)
// --------------------------------------------------------------------------- //

/** One closed M5 bar, priced in integer ticks. */
export interface Bar {
  /** UTC ISO-8601, the bar's open time. */
  readonly t: string;
  readonly o: number;
  readonly h: number;
  readonly l: number;
  readonly c: number;
  readonly v: number;
}

/** A level the sweep was drawn against. */
export interface Sweep {
  readonly direction: Side;
  /** Integer ticks: the level that was pierced. */
  readonly level: number;
  /** Integer ticks: how far through it traded. */
  readonly breach: number;
  /** Integer ticks: where it closed back, reclaiming the level. */
  readonly closeBack: number;
  readonly confirmedAt: string | null;
  /** Half-open [from, to) UTC window the marker is drawn across. */
  readonly fromUtc: string | null;
  readonly toUtc: string | null;
}

/** A close through the most recent swing point, on a body worth reading. */
export interface Mss {
  readonly direction: Side;
  /** Integer ticks: the swing point that broke. */
  readonly brokenSwing: number;
  readonly close: number;
  readonly body: number;
  readonly atr: number;
  readonly confirmedAt: string | null;
  readonly fromUtc: string | null;
  readonly toUtc: string | null;
}

/** A three-bar imbalance, and the midpoint its limit entry rests at. */
export interface Fvg {
  readonly direction: Side;
  /** Integer ticks. */
  readonly top: number;
  readonly bottom: number;
  readonly midpoint: number;
  readonly size: number;
  readonly confirmedAt: string | null;
  readonly fromUtc: string | null;
  readonly toUtc: string | null;
}

/** A session or fractal level the chart may draw. */
export interface Level {
  readonly kind:
    | "ASIAN_RANGE"
    | "LONDON_RANGE"
    | "PREVIOUS_DAY"
    | "SWING_HIGH"
    | "SWING_LOW";
  /** Integer ticks, or null when the level is a range rather than a line. */
  readonly ticks: number | null;
  readonly fromUtc: string | null;
  readonly toUtc: string | null;
}

/** `setups.json`, everything the setup visualizer draws. */
export interface SetupsPayload {
  readonly generatedAt: string | null;
  readonly bars: readonly Bar[];
  readonly window: WindowName | null;
  readonly sweep: Sweep | null;
  readonly mss: Mss | null;
  readonly fvg: Fvg | null;
  readonly levels: readonly Level[];
  readonly order: {
    readonly orderId: string;
    readonly entry: number;
    readonly stop: number;
    readonly target: number;
    readonly expiresAt: string | null;
  } | null;
}

// --------------------------------------------------------------------------- //
// /api/calendar  ->  calendar.json  (docs/PHASE_5X_SPEC.md §5)
// --------------------------------------------------------------------------- //

/** A scheduled economic release. */
export interface CalendarEvent {
  readonly name: string;
  readonly impact: "high" | "medium" | "low";
  /** Only a FRED-confirmed `RELEASE` may gate anything. */
  readonly kind: "RELEASE" | "ADVISORY";
  /** UTC ISO-8601, or null for a whole-day release with no meaningful instant. */
  readonly timestampUtc: string | null;
  /** New York local date, `YYYY-MM-DD`. */
  readonly localDate: string;
  readonly blackoutBeforeSeconds: number;
  readonly blackoutAfterSeconds: number;
}

/** A half-open UTC interval during which signals must not be generated. */
export interface Blackout {
  readonly name: string;
  readonly startUtc: string | null;
  readonly endUtc: string | null;
  readonly blocking: boolean;
}

/** `calendar.json`. */
export interface CalendarPayload {
  readonly generatedAt: string | null;
  readonly events: readonly CalendarEvent[];
  readonly blackouts: readonly Blackout[];
}

// --------------------------------------------------------------------------- //
// the assembled snapshot
// --------------------------------------------------------------------------- //

/** What every `fetchSnapshot` answer carries, whatever answered. */
export interface EngineSnapshot {
  readonly status: StatusPayload | null;
  readonly trades: TradesPayload | null;
  readonly metrics: MetricsPayload | null;
  readonly health: HealthPayload | null;
  readonly setups: SetupsPayload | null;
  readonly calendar: CalendarPayload | null;
  /** Which relative endpoints answered 200, keyed by endpoint name. */
  readonly available: Readonly<Record<string, boolean>>;
  readonly fetchedAt: Date;
}

/** The relative endpoints, and nothing else.

The dashboard never builds an absolute URL: it is served by the same origin as the API
(`api/main.py` binds `127.0.0.1:8080` and mounts `web/dist`), so a relative path is both
the correct and the only choice. An absolute URL here would be a dashboard that points at
a hardcoded development host.
*/
export const ENDPOINTS = {
  status: "/api/status",
  trades: "/api/trades",
  metrics: "/api/metrics",
  health: "/api/health",
  setups: "/api/setups",
  calendar: "/api/calendar",
} as const satisfies Readonly<Record<string, string>>;

/** `/api/setups` and `/api/calendar` are Phase 5.x endpoints that do not exist yet. */
export const SPEC_ONLY_ENDPOINTS: ReadonlySet<string> = new Set([
  ENDPOINTS.setups,
  ENDPOINTS.calendar,
]);

/** An endpoint that answered is one the engine build actually serves. */
export function isServed(endpoint: string): boolean {
  return !SPEC_ONLY_ENDPOINTS.has(endpoint);
}
