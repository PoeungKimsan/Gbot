/**
 * The client's contract: relative endpoints, honest nulls, and no synthesis.
 *
 * The first section is the one that matters most to the engine team. If the dashboard ever
 * starts building `http://127.0.0.1:8080/api/status` from a constant, this suite fails, and
 * the dashboard will be pointing at a developer's laptop in production. The API and the
 * dashboard are one origin — `api/main.py` binds `127.0.0.1:8080` and mounts `web/dist` —
 * so a relative path is both correct and the only safe choice.
 *
 * The rest pins the normalizers: a field the payload does not carry comes back `null`, never
 * zero, never an empty string, never a fabricated default. `docs/PHASE_5X_SPEC.md` §1 is a
 * spec for what *should* be there; this suite is the proof that the dashboard will never
 * pretend it already is.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ENDPOINTS, isServed, MINIMUM_SAMPLE_SIZE } from "./contract";
import {
  fetchSnapshot,
  intervalBounds,
  intervalIsReportable,
  normalizeMetrics,
  normalizeStatus,
  normalizeTrades,
} from "./client";
import { stubApi } from "../test/render";
import { METRICS_COMPLETE, publishedSnapshot } from "./fixtures";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the endpoints", () => {
  it("are all relative", () => {
    for (const endpoint of Object.values(ENDPOINTS)) {
      expect(endpoint.startsWith("/api/")).toBe(true);
      expect(endpoint).not.toMatch(/^https?:\/\//);
      expect(endpoint).not.toContain("127.0.0.1");
      expect(endpoint).not.toContain("localhost");
      expect(endpoint).not.toContain(":8080");
    }
  });

  it("covers the four endpoints the engine serves today", () => {
    expect(Object.values(ENDPOINTS)).toEqual(
      expect.arrayContaining([
        "/api/status",
        "/api/trades",
        "/api/metrics",
        "/api/health",
      ]),
    );
  });

  it("marks the Phase 5.x endpoints as not served", () => {
    expect(isServed(ENDPOINTS.status)).toBe(true);
    expect(isServed(ENDPOINTS.setups)).toBe(false);
    expect(isServed(ENDPOINTS.calendar)).toBe(false);
  });
});

describe("fetchSnapshot", () => {
  it("asks only relative endpoints, in parallel", async () => {
    const api = stubApi({
      status: { generated_at: new Date().toISOString() },
      trades: {},
      metrics: {},
      health: {},
    });
    await fetchSnapshot();

    expect(api.urls().every((url) => url.startsWith("/api/"))).toBe(true);
    expect(api.urls()).toHaveLength(Object.keys(ENDPOINTS).length);
  });

  it("answers 404 for the Phase 5.x endpoints without treating them as an error", async () => {
    const api = stubApi({ status: {}, trades: {}, metrics: {}, health: {} });
    const snapshot = await fetchSnapshot();

    expect(api.urls()).toContain("/api/setups");
    expect(api.urls()).toContain("/api/calendar");
    expect(snapshot.setups).toBeNull();
    expect(snapshot.calendar).toBeNull();
    expect(snapshot.available["/api/status"]).toBe(true);
    expect(snapshot.available["/api/setups"]).toBe(false);
  });

  it("treats a 503 snapshot as absent rather than fatal", async () => {
    stubApi({ status: null, trades: null, metrics: null, health: null });
    const snapshot = await fetchSnapshot();

    expect(snapshot.status).toBeNull();
    expect(snapshot.trades).toBeNull();
    expect(snapshot.metrics).toBeNull();
    expect(snapshot.health).toBeNull();
  });

  it("survives fetch throwing outright", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("connection refused");
      }),
    );
    const snapshot = await fetchSnapshot();

    expect(snapshot.status).toBeNull();
    expect(snapshot.available["/api/health"]).toBe(false);
  });
});

describe("normalizeStatus", () => {
  it("keeps every published field", () => {
    const status = normalizeStatus({
      run_id: "run-1",
      strategy_version: "abc",
      strategy_version_algorithm: "sha256",
      equity: "100003.40",
      bars: 100,
      open_positions: 1,
      resting_orders: 2,
      generated_at: "2026-03-09T12:00:00+00:00",
    });

    expect(status).toEqual({
      runId: "run-1",
      strategyVersion: "abc",
      strategyVersionAlgorithm: "sha256",
      equity: "100003.40",
      bars: 100,
      openPositions: 1,
      restingOrders: 2,
      generatedAt: "2026-03-09T12:00:00+00:00",
      state: "UNKNOWN",
      stateDetail: null,
      blockedReason: null,
      killSwitch: null,
      instrument: null,
    });
  });

  it("folds the supervisor's feed vocabulary onto the dashboard's", () => {
    expect(normalizeStatus({ state: "RUNNING" }).state).toBe("ACTIVE");
    expect(normalizeStatus({ state: "paused" }).state).toBe("PAUSED");
    expect(normalizeStatus({ state: "BLACKOUT" }).state).toBe("NEWS_BLACKOUT");
    expect(normalizeStatus({ state: "FAILED" }).state).toBe("STALE_FEED");
    expect(normalizeStatus({ state: "STOPPED" }).state).toBe("STALE_FEED");
    expect(normalizeStatus({ state: "SOMETHING_NEW" }).state).toBe("UNKNOWN");
    expect(normalizeStatus({}).state).toBe("UNKNOWN");
  });

  it("reads the kill-switch and the instrument when they are published", () => {
    const status = normalizeStatus({
      kill_switch: { tripped: true, trading_day: "2026-03-10", loss_r: "-2.5", reason: "daily loss" },
      instrument: { name: "XAU_USD", tick_size: "0.01", pip_size: "0.01", trade_units_precision: 0 },
    });

    expect(status.killSwitch?.tripped).toBe(true);
    expect(status.killSwitch?.lossR).toBe("-2.5");
    expect(status.instrument?.tickSize).toBe("0.01");
  });

  it("rejects an instrument without a tick size rather than guessing one", () => {
    expect(normalizeStatus({ instrument: { name: "XAU_USD" } }).instrument).toBeNull();
  });

  it("treats an unparseable timestamp as absent, not as the epoch", () => {
    expect(normalizeStatus({ generated_at: "not a date" }).generatedAt).toBeNull();
    expect(normalizeStatus({ generated_at: "" }).generatedAt).toBeNull();
  });
});

describe("normalizeTrades", () => {
  it("maps the published fields and leaves the Phase 5.x ones null", () => {
    const trades = normalizeTrades({
      count: 1,
      trades: [
        {
          trade_id: "p-1",
          side: "LONG",
          quantity: "1",
          pnl: "3.40",
          price: "2009.75",
          cost: "2006.35",
          r_multiple: "2",
          exit_reason: "TARGET",
          reference: "ASIAN",
        },
      ],
    });

    const row = trades.trades[0]!;
    expect(row.tradeId).toBe("p-1");
    expect(row.pnl).toBe("3.40");
    expect(row.realizedR).toBe("2");
    expect(row.exitReason).toBe("TARGET");
    expect(row.stopLoss).toBeNull();
    expect(row.takeProfit).toBeNull();
    expect(row.slippage).toBeNull();
    expect(row.window).toBeNull();
    expect(row.filledAt).toBeNull();
  });

  it("reads the entry and exit prices the publisher actually writes", () => {
    // `trades.json` spells them `cost` and `price`, both decimal strings. Confusing wire
    // names, but `supervisor._record_trade` is where they are decided.
    const trades = normalizeTrades({
      trades: [{ trade_id: "p-1", cost: "2006.35", price: "2009.75" }],
    });
    const row = trades.trades[0]!;
    expect(row.entryPrice).toBe("2006.35");
    expect(row.exitPrice).toBe("2009.75");
  });

  it("reads integer-tick prices for the Phase 5.x columns", () => {
    const trades = normalizeTrades({
      trades: [{ trade_id: "p-1", stop_loss: 200_455, take_profit: 201_815 }],
    });
    const row = trades.trades[0]!;
    expect(row.stopLoss).toBe(200_455);
    expect(row.takeProfit).toBe(201_815);
  });

  it("rejects a fractional tick count rather than rounding it", () => {
    const trades = normalizeTrades({ trades: [{ trade_id: "p-1", entry_price: 200.5 }] });
    expect(trades.trades[0]!.entryPrice).toBeNull();
  });

  it("survives an empty or malformed payload", () => {
    expect(normalizeTrades({}).trades).toEqual([]);
    expect(normalizeTrades({ trades: "not a list" }).trades).toEqual([]);
    expect(normalizeTrades({ trades: [null, 3, "x"] }).trades).toEqual([]);
    expect(normalizeTrades(null).trades).toEqual([]);
  });
});

describe("normalizeMetrics", () => {
  it("keeps the reporting boundary's floats as floats", () => {
    const metrics = normalizeMetrics({
      status: "COMPLETE",
      trade_count: 42,
      realized_pnl: 3.4,
      max_drawdown: 180.0,
      win_rate: 1.0,
      profit_factor: null,
      expectancy_r: 2.0,
      confidence_interval: { lower: 1.82, upper: 2.14 },
    });

    expect(metrics.expectancyR).toBe(2);
    expect(metrics.confidenceInterval).toEqual({ lower: 1.82, upper: 2.14 });
    expect(metrics.profitFactor).toBeNull();
  });

  it("treats a missing interval as null rather than as zeros", () => {
    expect(normalizeMetrics({ status: "INSUFFICIENT_DATA" }).confidenceInterval).toBeNull();
    expect(
      normalizeMetrics({ status: "COMPLETE", confidence_interval: { lower: 1 } })
        .confidenceInterval,
    ).toBeNull();
  });

  it("reads a payload whose status string is missing as insufficient", () => {
    const metrics = normalizeMetrics({ trade_count: 42, confidence_interval: { lower: 1, upper: 2 } });
    expect(metrics.status).toBe("INSUFFICIENT_DATA");
  });
});

describe("the reportability rule", () => {
  it("requires the floor, the status and the interval", () => {
    expect(intervalIsReportable(METRICS_COMPLETE)).toBe(true);
    expect(intervalIsReportable({ ...METRICS_COMPLETE, status: "INSUFFICIENT_DATA" })).toBe(false);
    expect(intervalIsReportable({ ...METRICS_COMPLETE, confidenceInterval: null })).toBe(false);
    expect(intervalIsReportable({ ...METRICS_COMPLETE, tradeCount: 29 })).toBe(false);
    expect(intervalIsReportable({ ...METRICS_COMPLETE, tradeCount: 30 })).toBe(true);
  });

  it("is anchored on the engine's own floor", () => {
    expect(MINIMUM_SAMPLE_SIZE).toBe(30);
  });

  it("returns the bounds only when they are reportable", () => {
    expect(intervalBounds(METRICS_COMPLETE)).toEqual(METRICS_COMPLETE.confidenceInterval);
    expect(intervalBounds({ ...METRICS_COMPLETE, tradeCount: 3 })).toBeNull();
  });
});

describe("the snapshot the current engine build produces", () => {
  it("has no engine state and no setups or calendar", () => {
    const snapshot = publishedSnapshot();
    expect(snapshot.status?.state).toBe("UNKNOWN");
    expect(snapshot.setups).toBeNull();
    expect(snapshot.calendar).toBeNull();
    expect(snapshot.trades?.trades[0]?.stopLoss).toBeNull();
  });

  it("still carries the published performance figures", () => {
    const snapshot = publishedSnapshot();
    expect(snapshot.metrics?.tradeCount).toBe(42);
    expect(snapshot.metrics?.realizedPnl).toBeGreaterThan(0);
  });
});
