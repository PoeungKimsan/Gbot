/**Fixtures, built to the full contract — including the Phase 5.x fields.

These exist so every view in this dashboard is exercised with *real-shaped data* rather
than only with holes. A candlestick chart that has only ever been rendered against `null`
is a chart nobody has looked at.

Nothing here is served in a production build. `App` never imports this file; the renderer
takes an injected snapshot, and the fixtures are the injection the tests and the `?fixtures`
developer affordance use. The fixtures carry the Phase 5.x fields the current engine build
does not publish, so turning them on shows what the dashboard will look like when
`docs/PHASE_5X_SPEC.md` has been implemented — and what it looks like without them.

Everything is deterministic. The trade series is drawn from a fixed-seed generator and the
bar series is generated from a fixed seed too, so a rendered screenshot is reproducible
byte for byte.
*/

import {
  type Bar,
  type CalendarEvent,
  type CalendarPayload,
  type EngineSnapshot,
  type MetricsPayload,
  type SetupsPayload,
  type StatusPayload,
  type TradesPayload,
  type TradeRecord,
} from "./contract";
import { normalizeMetrics, normalizeStatus, normalizeTrades } from "./client";

// --------------------------------------------------------------------------- //
// the clock
// --------------------------------------------------------------------------- //

/** The instant the fixtures are built around, so the calendar and the bars line up. */
export const DEMO_NOW: Date = new Date("2026-03-10T07:40:00.000Z");

// --------------------------------------------------------------------------- //
// instrument
// --------------------------------------------------------------------------- //

export const TICK_SIZE = "0.01";

// --------------------------------------------------------------------------- //
// status
// --------------------------------------------------------------------------- //

export const STATUS_FIXTURE: StatusPayload = {
  runId: "run-2026-03-10-london",
  strategyVersion: "9f2c4a7b1e5d8034c6a9b2f7d4e1c8a03b5f6d9e2c7a4b1f8d3e6c0a5b9f2d1e",
  strategyVersionAlgorithm: "sha256",
  equity: "100418.60",
  bars: 512,
  openPositions: 1,
  restingOrders: 1,
  generatedAt: "2026-03-10T07:39:52.000Z",
  state: "ACTIVE",
  stateDetail: "new entries permitted",
  blockedReason: "",
  killSwitch: null,
  instrument: {
    name: "XAU_USD",
    tickSize: TICK_SIZE,
    pipSize: TICK_SIZE,
    tradeUnitsPrecision: 0,
  },
};

// --------------------------------------------------------------------------- //
// trades
// --------------------------------------------------------------------------- //

/** A fixed-seed linear congruential generator. Same stream, every run. */
function generator(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    return state / 0x1_0000_0000;
  };
}

/** Integer ticks, as the web layer's `price()` renders them. */
function tickText(ticks: number): string {
  return (ticks / 100).toFixed(2);
}

/** One closed trade, `offsetMinutes` from `DEMO_NOW`. */
function trade(index: number, offsetMinutes: number, won: boolean): TradeRecord {
  const base = DEMO_NOW.getTime() + offsetMinutes * 60_000;
  const entry = 200_640 + index * 3;
  const risk = 180; // ticks between entry and stop => $1.80 at a $0.01 tick
  const rMultiple = won ? 2 + (index % 3) * 0.25 : -1;
  return {
    tradeId: `p-${(index + 1).toString().padStart(4, "0")}`,
    side: index % 3 === 0 ? "SHORT" : "LONG",
    window: index % 2 === 0 ? "LONDON" : "NY_AM",
    // Published today as `cost`/`price`: decimal strings, not tick counts.
    entryPrice: tickText(entry),
    exitPrice: tickText(won ? entry + risk * 2 : entry - risk),
    stopLoss: entry - risk,
    takeProfit: entry + risk * 2,
    pnl: rMultiple === -1 ? "-1.80" : "3.60",
    slippage: index % 2 === 0 ? "-0.24" : "-0.08",
    slippageTicks: index % 2 === 0 ? 24 : 8,
    realizedR: rMultiple.toFixed(2),
    quantity: "1",
    exitReason: won ? "TARGET" : "STOP_LOSS",
    reference: index % 2 === 0 ? "ASIAN" : "LONDON",
    filledAt: new Date(base).toISOString(),
    placedAt: new Date(base - 3_600_000).toISOString(),
  };
}

/** A closed-trade series long enough for a bootstrap interval to be reported. */
export function tradeSeries(): TradeRecord[] {
  const random = generator(20_260_115);
  const count = 42;
  const records: TradeRecord[] = [];
  for (let index = 0; index < count; index += 1) {
    const won = random() > 0.42;
    records.push(trade(index, -(count - index) * 37, won));
  }
  return records;
}

export const TRADES_FIXTURE: TradesPayload = {
  count: 42,
  trades: tradeSeries(),
};

// --------------------------------------------------------------------------- //
// metrics, computed from the fixture trades rather than typed in
// --------------------------------------------------------------------------- //

function metricsOf(trades: readonly TradeRecord[]): MetricsPayload {
  const count = trades.length;
  const realizedPnl = trades.reduce((total, entry) => total + Number(entry.pnl), 0);
  const multiples = trades.map((entry) => Number(entry.realizedR));
  const expectancyR = multiples.reduce((total, entry) => total + entry, 0) / count;
  const wins = trades.filter((entry) => Number(entry.realizedR) > 0).length;
  const winRate = wins / count;

  let equity = 0;
  let peak = 0;
  let drawdown = 0;
  for (const entry of trades) {
    equity += Number(entry.pnl);
    peak = Math.max(peak, equity);
    drawdown = Math.max(drawdown, peak - equity);
  }

  const grossProfit = trades
    .filter((entry) => Number(entry.pnl) > 0)
    .reduce((total, entry) => total + Number(entry.pnl), 0);
  const grossLoss = -trades
    .filter((entry) => Number(entry.pnl) < 0)
    .reduce((total, entry) => total + Number(entry.pnl), 0);

  const complete = count >= 30;
  return {
    status: complete ? "COMPLETE" : "INSUFFICIENT_DATA",
    tradeCount: count,
    realizedPnl,
    maxDrawdown: drawdown,
    winRate,
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : null,
    expectancyR,
    confidenceInterval: complete
      ? { lower: expectancyR - 0.18, upper: expectancyR + 0.22 }
      : null,
  };
}

/** The report for the full series: a sample large enough for bounds to be printed. */
export const METRICS_COMPLETE: MetricsPayload = metricsOf(tradeSeries());

/** The report for a short series: the `INSUFFICIENT_DATA` path. */
export const METRICS_INSUFFICIENT: MetricsPayload = metricsOf(tradeSeries().slice(0, 12));

// --------------------------------------------------------------------------- //
// setups
// --------------------------------------------------------------------------- //

/** M5 bars across the London silver-bullet window, in integer ticks. */
function barSeries(): Bar[] {
  const random = generator(20_260_310);
  const bars: Bar[] = [];
  // 06:30 UTC -- the window opens at 07:00 UTC, 03:00 in New York on EDT.
  let close = 200_640;
  for (let index = 0; index < 24; index += 1) {
    const drift = index < 8 ? 6 : index < 12 ? -14 : index < 16 ? 26 : -4;
    const open = close;
    close = open + drift + Math.round((random() - 0.5) * 12);
    const high = Math.max(open, close) + 4 + Math.round(random() * 10);
    const low = Math.min(open, close) - 4 - Math.round(random() * 10);
    bars.push({
      t: new Date(DEMO_NOW.getTime() - (23 - index) * 300_000).toISOString(),
      o: open,
      h: high,
      l: low,
      c: close,
      v: 400 + Math.round(random() * 220),
    });
  }
  return bars;
}

const BARS = barSeries();

export const SETUPS_FIXTURE: SetupsPayload = {
  generatedAt: "2026-03-10T07:39:50.000Z",
  bars: BARS,
  window: "LONDON",
  sweep: {
    direction: "LONG",
    level: 200_612,
    breach: 200_606,
    closeBack: 200_619,
    confirmedAt: BARS[12]?.t ?? null,
    fromUtc: BARS[11]?.t ?? null,
    toUtc: BARS[13]?.t ?? null,
  },
  mss: {
    direction: "LONG",
    brokenSwing: 200_692,
    close: 200_701,
    body: 26,
    atr: 14,
    confirmedAt: BARS[15]?.t ?? null,
    fromUtc: BARS[14]?.t ?? null,
    toUtc: BARS[16]?.t ?? null,
  },
  fvg: {
    direction: "LONG",
    top: 200_736,
    bottom: 200_724,
    midpoint: 200_730,
    size: 12,
    confirmedAt: BARS[17]?.t ?? null,
    fromUtc: BARS[16]?.t ?? null,
    toUtc: BARS[18]?.t ?? null,
  },
  levels: [
    {
      kind: "ASIAN_RANGE",
      ticks: null,
      fromUtc: new Date(DEMO_NOW.getTime() - 11 * 3_600_000).toISOString(),
      toUtc: new Date(DEMO_NOW.getTime() - 7 * 3_600_000).toISOString(),
    },
    {
      kind: "SWING_LOW",
      ticks: 200_612,
      fromUtc: BARS[10]?.t ?? null,
      toUtc: BARS[23]?.t ?? null,
    },
    {
      kind: "SWING_HIGH",
      ticks: 200_692,
      fromUtc: BARS[6]?.t ?? null,
      toUtc: BARS[23]?.t ?? null,
    },
  ],
  order: {
    orderId: "o-0007",
    entry: 200_730,
    stop: 200_598,
    target: 200_994,
    expiresAt: BARS[23]?.t ?? null,
  },
};

// --------------------------------------------------------------------------- //
// calendar
// --------------------------------------------------------------------------- //

/** The schedule, offset in seconds from a reference instant. */
function event(
  name: string,
  offsetSeconds: number,
  beforeSeconds: number,
  afterSeconds: number,
  kind: "RELEASE" | "ADVISORY" = "RELEASE",
): CalendarEvent {
  return {
    name,
    impact: "high",
    kind,
    timestampUtc: new Date(DEMO_NOW.getTime() + offsetSeconds * 1000).toISOString(),
    localDate: "2026-03-10",
    blackoutBeforeSeconds: beforeSeconds,
    blackoutAfterSeconds: afterSeconds,
  };
}

export const CALENDAR_FIXTURE: CalendarPayload = {
  generatedAt: "2026-03-10T07:39:00.000Z",
  events: [
    event("Nonfarm Payrolls", -3_600, 1_800, 5_400),
    event("Consumer Price Index", 9_000, 1_800, 5_400),
    event("FOMC Statement", 32_400, 3_600, 10_800),
    event("Unemployment Rate", 5_400, 900, 1_800, "ADVISORY"),
  ],
  blackouts: [
    {
      name: "Nonfarm Payrolls",
      startUtc: new Date(DEMO_NOW.getTime() - 3_600_000 + 1_800_000).toISOString(),
      endUtc: new Date(DEMO_NOW.getTime() - 3_600_000 + 5_400_000).toISOString(),
      blocking: true,
    },
    {
      name: "Consumer Price Index",
      startUtc: new Date(DEMO_NOW.getTime() + 9_000_000 - 1_800_000).toISOString(),
      endUtc: new Date(DEMO_NOW.getTime() + 9_000_000 + 5_400_000).toISOString(),
      blocking: true,
    },
  ],
};

// --------------------------------------------------------------------------- //
// the assembled snapshots
// --------------------------------------------------------------------------- //

/** Everything answering, at the fixture clock. */
export function fullSnapshot(now: Date = DEMO_NOW): EngineSnapshot {
  void now;
  return {
    status: STATUS_FIXTURE,
    trades: TRADES_FIXTURE,
    metrics: METRICS_COMPLETE,
    health: { status: "ok", snapshots: ["metrics.json", "state.json", "trades.json"] },
    setups: SETUPS_FIXTURE,
    calendar: CALENDAR_FIXTURE,
    available: {
      "/api/status": true,
      "/api/trades": true,
      "/api/metrics": true,
      "/api/health": true,
      "/api/setups": true,
      "/api/calendar": true,
    },
    fetchedAt: new Date(now.getTime() - 6_000),
  };
}

/**
 * The snapshot the *current* engine build can actually produce: the three published
 * documents, with every Phase 5.x field absent. Rendering this is what the real
 * dashboard does today.
 */
export function publishedSnapshot(now: Date = DEMO_NOW): EngineSnapshot {
  const status = normalizeStatus({
    run_id: STATUS_FIXTURE.runId,
    strategy_version: STATUS_FIXTURE.strategyVersion,
    strategy_version_algorithm: STATUS_FIXTURE.strategyVersionAlgorithm,
    equity: STATUS_FIXTURE.equity,
    bars: STATUS_FIXTURE.bars,
    open_positions: STATUS_FIXTURE.openPositions,
    resting_orders: STATUS_FIXTURE.restingOrders,
    generated_at: STATUS_FIXTURE.generatedAt,
  });
  const trades = normalizeTrades({
    count: TRADES_FIXTURE.count,
    trades: TRADES_FIXTURE.trades.map((entry) => ({
      trade_id: entry.tradeId,
      side: entry.side,
      quantity: entry.quantity,
      pnl: entry.pnl,
      price: (entry.exitPrice ?? "0").toString(),
      cost: (entry.entryPrice ?? "0").toString(),
      r_multiple: entry.realizedR,
      exit_reason: entry.exitReason,
      reference: entry.reference,
    })),
  });
  return {
    status,
    trades,
    metrics: normalizeMetrics({
      status: METRICS_COMPLETE.status,
      trade_count: METRICS_COMPLETE.tradeCount,
      realized_pnl: METRICS_COMPLETE.realizedPnl,
      max_drawdown: METRICS_COMPLETE.maxDrawdown,
      win_rate: METRICS_COMPLETE.winRate,
      profit_factor: METRICS_COMPLETE.profitFactor,
      expectancy_r: METRICS_COMPLETE.expectancyR,
      confidence_interval: METRICS_COMPLETE.confidenceInterval,
    }),
    health: { status: "ok", snapshots: ["metrics.json", "state.json", "trades.json"] },
    setups: null,
    calendar: null,
    available: {
      "/api/status": true,
      "/api/trades": true,
      "/api/metrics": true,
      "/api/health": true,
      "/api/setups": false,
      "/api/calendar": false,
    },
    fetchedAt: new Date(now.getTime() - 6_000),
  };
}
