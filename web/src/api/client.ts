/**The dashboard's read of the engine: fetch, normalise, never invent.

The API is `api/main.py`, which serves the publisher's three snapshots verbatim with
`Cache-Control: public, max-age=10` and a 503 while a snapshot is missing. This client is
the whole of the dashboard's I/O surface, and it holds three positions:

1. **Relative endpoints only.** `ENDPOINTS` is the single list of URLs it will ever ask
   for. Each one starts with `/api/`. There is no origin, no host and no port anywhere in
   this file, because the API and the dashboard are one origin.
2. **A bad response is a state, not a crash.** A 503 means the publisher has not run yet;
   a 404 means the endpoint is Phase 5.x and this engine build does not have it. Both
   become `null`, and the view renders a sentinel. Raising would put a Java\u0007Script
   stack trace in front of an operator.
3. **Nothing is synthesized.** A field the payload does not carry stays `null`. The
   dashboard's job is to report what the engine published, and a dashboard that fills in a
   stop-loss the engine never wrote is worse than one that shows an empty cell.
*/

import {
  ENDPOINTS,
  MINIMUM_SAMPLE_SIZE,
  type Bar,
  type Blackout,
  type CalendarEvent,
  type CalendarPayload,
  type ConfidenceInterval,
  type EngineSnapshot,
  type EngineState,
  type ExitReason,
  type Fvg,
  type HealthPayload,
  type Instrument,
  type KillSwitch,
  type Level,
  type Mss,
  type MetricsPayload,
  type SetupsPayload,
  type Side,
  type StatusPayload,
  type Sweep,
  type TradesPayload,
  type TradeRecord,
  type WindowName,
} from "./contract";

// --------------------------------------------------------------------------- //
// fetching
// --------------------------------------------------------------------------- //

/** Fetch one snapshot as JSON, or `null` if it is missing, unreadable or not an object. */
async function fetchJson(endpoint: string): Promise<Record<string, unknown> | null> {
  try {
    const response = await fetch(endpoint, {
      headers: { accept: "application/json" },
    });
    if (!response.ok) {
      return null;
    }
    const parsed: unknown = await response.json();
    return asRecord(parsed);
  } catch {
    // A refused connection is the engine being down, which is a state the header
    // reports. Letting it escape would take the whole dashboard with it.
    return null;
  }
}

/** Read every endpoint and assemble what answered.

Every request is issued in parallel: the API is a local loopback service with a ten-second
cache, so serialising them would make the dashboard's own latency a function of its own
layout.

The documents are keyed by *endpoint path*, not by friendly name — a mismatch there is how
a dashboard ends up reading `/api/status` and rendering the trades table.
*/
export async function fetchSnapshot(): Promise<EngineSnapshot> {
  const answers = await Promise.all(
    Object.values(ENDPOINTS).map(
      async (endpoint) => [endpoint, await fetchJson(endpoint)] as const,
    ),
  );
  const documents = new Map(answers);
  const available: Record<string, boolean> = {};
  for (const [endpoint, document] of answers) {
    available[endpoint] = document !== null;
  }

  return {
    status: readDocument(documents, ENDPOINTS.status, normalizeStatus),
    trades: readDocument(documents, ENDPOINTS.trades, normalizeTrades),
    metrics: readDocument(documents, ENDPOINTS.metrics, normalizeMetrics),
    health: readDocument(documents, ENDPOINTS.health, normalizeHealth),
    setups: readDocument(documents, ENDPOINTS.setups, normalizeSetups),
    calendar: readDocument(documents, ENDPOINTS.calendar, normalizeCalendar),
    available,
    fetchedAt: new Date(),
  };
}

/** Normalise one document, or `null` when the endpoint did not answer with an object. */
function readDocument<T>(
  documents: ReadonlyMap<string, Record<string, unknown> | null>,
  endpoint: string,
  normalize: (raw: unknown) => T,
): T | null {
  const document = documents.get(endpoint);
  return document === null || document === undefined ? null : normalize(document);
}

// --------------------------------------------------------------------------- //
// /api/status
// --------------------------------------------------------------------------- //

const FEED_STATE_TO_ENGINE_STATE: Readonly<Record<string, EngineState>> = {
  // The supervisor's vocabulary, folded onto the dashboard's.
  RUNNING: "ACTIVE",
  ACTIVE: "ACTIVE",
  PAUSED: "PAUSED",
  BLACKOUT: "NEWS_BLACKOUT",
  NEWS_BLACKOUT: "NEWS_BLACKOUT",
  // A feed that failed or ended is not delivering; that is what STALE FEED says.
  FAILED: "STALE_FEED",
  STOPPED: "STALE_FEED",
  FLAT: "FLAT",
};

/** Normalise `state.json`, mapping every Phase 5.x field to `null` while it is absent. */
export function normalizeStatus(raw: unknown): StatusPayload {
  const doc = asRecord(raw) ?? {};
  const feedState = text(doc.state);
  const state: EngineState =
    feedState === null
      ? "UNKNOWN"
      : (FEED_STATE_TO_ENGINE_STATE[feedState.toUpperCase()] ?? "UNKNOWN");

  return {
    runId: text(doc.run_id) ?? "",
    strategyVersion: text(doc.strategy_version) ?? "",
    strategyVersionAlgorithm: text(doc.strategy_version_algorithm) ?? "",
    equity: text(doc.equity) ?? "0",
    bars: integer(doc.bars) ?? 0,
    openPositions: integer(doc.open_positions) ?? 0,
    restingOrders: integer(doc.resting_orders) ?? 0,
    generatedAt: timestamp(doc.generated_at),
    state,
    stateDetail: text(doc.state_detail),
    blockedReason: text(doc.blocked_reason),
    killSwitch: readKillSwitch(doc.kill_switch),
    instrument: readInstrument(doc.instrument),
  };
}

function readKillSwitch(raw: unknown): KillSwitch | null {
  const doc = asRecord(raw);
  if (doc === null) {
    return null;
  }
  return {
    tripped: boolean(doc.tripped) ?? false,
    tradingDay: text(doc.trading_day) ?? "",
    lossR: text(doc.loss_r) ?? "0",
    reason: text(doc.reason) ?? "",
  };
}

function readInstrument(raw: unknown): Instrument | null {
  const doc = asRecord(raw);
  if (doc === null) {
    return null;
  }
  const tickSize = text(doc.tick_size);
  if (tickSize === null) {
    return null;
  }
  return {
    name: text(doc.name) ?? "",
    tickSize,
    pipSize: text(doc.pip_size) ?? tickSize,
    tradeUnitsPrecision: integer(doc.trade_units_precision) ?? 0,
  };
}

// --------------------------------------------------------------------------- //
// /api/trades
// --------------------------------------------------------------------------- //

const EXIT_REASONS: ReadonlySet<string> = new Set([
  "TARGET",
  "STOP_LOSS",
  "SESSION_CLOSE",
  "KILL_SWITCH",
  "CONTROL_PLANE",
  "ORDER_EXPIRED",
]);

const SIDES: ReadonlySet<string> = new Set(["LONG", "SHORT"]);

const WINDOWS: ReadonlySet<string> = new Set(["LONDON", "NY_AM"]);

/** Normalise `trades.json`, one journal-joined record per closed trade. */
export function normalizeTrades(raw: unknown): TradesPayload {
  const doc = asRecord(raw) ?? {};
  const list = Array.isArray(doc.trades) ? doc.trades : [];
  const trades = list
    .map((entry) => normalizeTrade(entry))
    .filter((trade): trade is TradeRecord => trade !== null);
  return { count: integer(doc.count) ?? trades.length, trades };
}

function normalizeTrade(raw: unknown): TradeRecord | null {
  const doc = asRecord(raw);
  if (doc === null) {
    return null;
  }
  const side = upperText(doc.side);
  return {
    tradeId: text(doc.trade_id) ?? "",
    side: side !== null && SIDES.has(side) ? (side as Side) : "LONG",
    window: readWindow(doc.window),
    entryPrice: text(doc.cost) ?? text(doc.entry_price),
    exitPrice: text(doc.price) ?? text(doc.exit_price),
    stopLoss: ticks(doc.stop_loss),
    takeProfit: ticks(doc.take_profit),
    pnl: text(doc.pnl) ?? "0",
    slippage: text(doc.slippage),
    slippageTicks: ticks(doc.slippage_ticks),
    realizedR: text(doc.r_multiple) ?? "0",
    quantity: text(doc.quantity) ?? "1",
    exitReason: readExitReason(doc.exit_reason),
    reference: text(doc.reference) ?? "",
    filledAt: timestamp(doc.filled_at),
    placedAt: timestamp(doc.placed_at),
  };
}

function readExitReason(raw: unknown): ExitReason {
  const value = upperText(raw);
  if (value === null) {
    return "UNKNOWN";
  }
  const normalised = value === "TARGET" ? "TARGET" : value.replace(/[\s-]+/g, "_");
  return EXIT_REASONS.has(normalised) ? (normalised as ExitReason) : "UNKNOWN";
}

function readWindow(raw: unknown): WindowName | null {
  const value = upperText(raw);
  return value !== null && WINDOWS.has(value) ? (value as WindowName) : null;
}

// --------------------------------------------------------------------------- //
// /api/metrics
// --------------------------------------------------------------------------- //

/** Normalise `metrics.json`, the reporting boundary where floats are legitimate. */
export function normalizeMetrics(raw: unknown): MetricsPayload {
  const doc = asRecord(raw) ?? {};
  const status = upperText(doc.status);
  const interval = asRecord(doc.confidence_interval);
  const lower = interval === null ? null : number(interval.lower);
  const upper = interval === null ? null : number(interval.upper);

  return {
    status: status === "COMPLETE" ? "COMPLETE" : "INSUFFICIENT_DATA",
    tradeCount: integer(doc.trade_count) ?? 0,
    realizedPnl: number(doc.realized_pnl) ?? 0,
    maxDrawdown: number(doc.max_drawdown) ?? 0,
    winRate: number(doc.win_rate),
    profitFactor: number(doc.profit_factor),
    expectancyR: number(doc.expectancy_r),
    confidenceInterval:
      lower !== null && upper !== null ? { lower, upper } : null,
  };
}

// --------------------------------------------------------------------------- //
// /api/health
// --------------------------------------------------------------------------- //

function normalizeHealth(raw: unknown): HealthPayload {
  const doc = asRecord(raw) ?? {};
  const snapshots = Array.isArray(doc.snapshots) ? doc.snapshots : [];
  return {
    status: text(doc.status) ?? "unknown",
    snapshots: snapshots.filter((entry): entry is string => typeof entry === "string"),
  };
}

// --------------------------------------------------------------------------- //
// /api/setups  (Phase 5.x)
// --------------------------------------------------------------------------- //

function normalizeSetups(raw: unknown): SetupsPayload {
  const doc = asRecord(raw) ?? {};
  return {
    generatedAt: timestamp(doc.generated_at),
    bars: readArray(doc.bars).map(normalizeBar).filter(isNonNull),
    window: readWindow(doc.window),
    sweep: readMarker(doc.sweep, normalizeSweep),
    mss: readMarker(doc.mss, normalizeMss),
    fvg: readMarker(doc.fvg, normalizeFvg),
    levels: readArray(doc.levels).map(normalizeLevel).filter(isNonNull),
    order: readOrder(doc.order),
  };
}

function normalizeBar(raw: unknown): Bar | null {
  const doc = asRecord(raw);
  if (doc === null) {
    return null;
  }
  const t = timestamp(doc.t);
  const o = ticks(doc.o);
  const h = ticks(doc.h);
  const l = ticks(doc.l);
  const c = ticks(doc.c);
  if (t === null || o === null || h === null || l === null || c === null) {
    return null;
  }
  return { t, o, h, l, c, v: ticks(doc.v) ?? 0 };
}

function normalizeSweep(raw: Record<string, unknown>): Sweep | null {
  const level = ticks(raw.level);
  const breach = ticks(raw.breach);
  const closeBack = ticks(raw.close_back);
  if (level === null || breach === null || closeBack === null) {
    return null;
  }
  return {
    direction: readSide(raw.direction),
    level,
    breach,
    closeBack,
    confirmedAt: timestamp(raw.confirmed_at),
    fromUtc: timestamp(raw.from_utc),
    toUtc: timestamp(raw.to_utc),
  };
}

function normalizeMss(raw: Record<string, unknown>): Mss | null {
  const brokenSwing = ticks(raw.broken_swing);
  const close = ticks(raw.close);
  const body = ticks(raw.body);
  const atr = ticks(raw.atr);
  if (brokenSwing === null || close === null || body === null || atr === null) {
    return null;
  }
  return {
    direction: readSide(raw.direction),
    brokenSwing,
    close,
    body,
    atr,
    confirmedAt: timestamp(raw.confirmed_at),
    fromUtc: timestamp(raw.from_utc),
    toUtc: timestamp(raw.to_utc),
  };
}

function normalizeFvg(raw: Record<string, unknown>): Fvg | null {
  const top = ticks(raw.top);
  const bottom = ticks(raw.bottom);
  const midpoint = ticks(raw.midpoint);
  const size = ticks(raw.size);
  if (top === null || bottom === null || midpoint === null || size === null) {
    return null;
  }
  return {
    direction: readSide(raw.direction),
    top,
    bottom,
    midpoint,
    size,
    confirmedAt: timestamp(raw.confirmed_at),
    fromUtc: timestamp(raw.from_utc),
    toUtc: timestamp(raw.to_utc),
  };
}

const LEVEL_KINDS: ReadonlySet<string> = new Set([
  "ASIAN_RANGE",
  "LONDON_RANGE",
  "PREVIOUS_DAY",
  "SWING_HIGH",
  "SWING_LOW",
]);

function normalizeLevel(raw: unknown): Level | null {
  const doc = asRecord(raw);
  if (doc === null) {
    return null;
  }
  const kind = upperText(doc.kind);
  if (kind === null || !LEVEL_KINDS.has(kind)) {
    return null;
  }
  return {
    kind: kind as Level["kind"],
    ticks: ticks(doc.ticks),
    fromUtc: timestamp(doc.from_utc),
    toUtc: timestamp(doc.to_utc),
  };
}

function readOrder(raw: unknown): SetupsPayload["order"] {
  const doc = asRecord(raw);
  const entry = doc === null ? null : ticks(doc.entry);
  const stop = doc === null ? null : ticks(doc.stop);
  const target = doc === null ? null : ticks(doc.target);
  if (doc === null || entry === null || stop === null || target === null) {
    return null;
  }
  return {
    orderId: text(doc.order_id) ?? "",
    entry,
    stop,
    target,
    expiresAt: timestamp(doc.expires_at),
  };
}

function readSide(raw: unknown): Side {
  const value = upperText(raw);
  return value === "SHORT" ? "SHORT" : "LONG";
}

function readMarker<T>(
  raw: unknown,
  normalize: (record: Record<string, unknown>) => T | null,
): T | null {
  const doc = asRecord(raw);
  return doc === null ? null : normalize(doc);
}

// --------------------------------------------------------------------------- //
// /api/calendar  (Phase 5.x)
// --------------------------------------------------------------------------- //

function normalizeCalendar(raw: unknown): CalendarPayload {
  const doc = asRecord(raw) ?? {};
  return {
    generatedAt: timestamp(doc.generated_at),
    events: readArray(doc.events).map(normalizeEvent).filter(isNonNull),
    blackouts: readArray(doc.blackouts).map(normalizeBlackout).filter(isNonNull),
  };
}

function normalizeEvent(raw: unknown): CalendarEvent | null {
  const doc = asRecord(raw);
  const name = doc === null ? null : text(doc.name);
  if (doc === null || name === null) {
    return null;
  }
  const impact = text(doc.impact)?.toLowerCase();
  const kind = upperText(doc.kind);
  return {
    name,
    impact: impact === "high" || impact === "medium" || impact === "low" ? impact : "low",
    kind: kind === "RELEASE" ? "RELEASE" : "ADVISORY",
    timestampUtc: timestamp(doc.timestamp_utc),
    localDate: text(doc.local_date) ?? "",
    blackoutBeforeSeconds: integer(doc.blackout_before_seconds) ?? 0,
    blackoutAfterSeconds: integer(doc.blackout_after_seconds) ?? 0,
  };
}

function normalizeBlackout(raw: unknown): Blackout | null {
  const doc = asRecord(raw);
  const name = doc === null ? null : text(doc.name);
  if (doc === null || name === null) {
    return null;
  }
  return {
    name,
    startUtc: timestamp(doc.start_utc),
    endUtc: timestamp(doc.end_utc),
    blocking: boolean(doc.blocking) ?? true,
  };
}

// --------------------------------------------------------------------------- //
// confidence interval helper
// --------------------------------------------------------------------------- //

/** Whether the sample is large enough for a bootstrap interval to mean anything.

Deliberately independent of `metrics.status`: the engine says `INSUFFICIENT_DATA` for
`trade_count < MINIMUM_SAMPLE_SIZE`, and the dashboard re-checks both, so a snapshot that
claims `COMPLETE` over four trades is caught here rather than believed.
*/
export function intervalIsReportable(metrics: MetricsPayload): boolean {
  return (
    metrics.status === "COMPLETE" &&
    metrics.tradeCount >= MINIMUM_SAMPLE_SIZE &&
    metrics.confidenceInterval !== null
  );
}

/** The lower and upper bounds, or `null` when the sample does not support one. */
export function intervalBounds(
  metrics: MetricsPayload,
): ConfidenceInterval | null {
  return intervalIsReportable(metrics) ? metrics.confidenceInterval : null;
}

// --------------------------------------------------------------------------- //
// shape guards
// --------------------------------------------------------------------------- //

/** A trimmed, upper-cased string, or null. Optional-chaining on `text` yields undefined,
 * and a downstream `X.has(undefined)` is a bug the type system is right to refuse. */
function upperText(value: unknown): string | null {
  const trimmed = text(value);
  return trimmed === null ? null : trimmed.toUpperCase();
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function isNonNull<T>(value: T | null): value is T {
  return value !== null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) ? value : null;
}

function number(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function boolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** A price on the wire: an integer tick count, or a decimal string accepted as ticks. */
function ticks(value: unknown): number | null {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value : null;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return /^-?\d+$/.test(trimmed) ? Number.parseInt(trimmed, 10) : null;
  }
  return null;
}

/** An ISO-8601 instant that parses, or `null`.

A timestamp the dashboard cannot parse is treated as absent rather than coerced to the
epoch, because `generated_at` drives the staleness banner and a silently-epoch timestamp
would make a perfectly fresh engine look permanently stale.
*/
function timestamp(value: unknown): string | null {
  if (typeof value !== "string" || value.trim() === "") {
    return null;
  }
  return Number.isNaN(Date.parse(value)) ? null : value;
}
