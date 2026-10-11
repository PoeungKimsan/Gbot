/**
 * The status header: state, provenance, and the staleness banner.
 *
 * The banner is the interesting one. `STALE_AFTER_SECONDS` is 30, which is three times the
 * API's own cache lifetime (`api/main.py` sends `public, max-age=10`). One second past the
 * cache is a slow refresh; thirty is a stopped publisher. The suite pins the boundary in
 * both directions, because a banner that fires on a healthy engine is a banner an operator
 * learns to ignore.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { StatusHeader } from "./StatusHeader";
import { STALE_AFTER_SECONDS, type EngineSnapshot, type StatusPayload } from "../api/contract";
import { STATUS_FIXTURE } from "../api/fixtures";

const FRESH = STATUS_FIXTURE;

/** A payload generated `seconds` ago. */
function aged(seconds: number, overrides: Partial<StatusPayload> = {}): StatusPayload {
  const moment = new Date(Date.now() - seconds * 1000);
  return {
    ...STATUS_FIXTURE,
    generatedAt: moment.toISOString(),
    ...overrides,
  };
}

function snapshotFor(status: StatusPayload | null): EngineSnapshot | null {
  if (status === null) {
    return null;
  }
  return {
    status,
    trades: null,
    metrics: null,
    health: null,
    setups: null,
    calendar: null,
    available: {},
    fetchedAt: new Date(),
  };
}

function renderHeader(status: StatusPayload | null) {
  return render(<StatusHeader status={status} snapshot={snapshotFor(status)} />);
}

afterEach(() => {
  vi.useRealTimers();
});

describe("the badge", () => {
  it("is prominent and says SIMULATED / PAPER TRADING", () => {
    renderHeader(FRESH);
    const badge = screen.getByTestId("simulated-badge");
    expect(badge).toHaveTextContent(/simulated \/ paper trading/i);
  });

  it("is present whatever the engine state is", () => {
    renderHeader(aged(0, { state: "PAUSED" }));
    expect(screen.getByTestId("simulated-badge")).toBeInTheDocument();
  });
});

describe("the engine state", () => {
  it.each([
    ["ACTIVE", "ACTIVE"],
    ["PAUSED", "PAUSED"],
    ["NEWS_BLACKOUT", "NEWS BLACKOUT"],
    ["STALE_FEED", "STALE FEED"],
  ])("renders %s as %s", (raw, label) => {
    renderHeader(aged(0, { state: raw as StatusPayload["state"] }));
    const node = screen.getByTestId("engine-state");
    expect(node).toHaveTextContent(label);
    expect(node.getAttribute("data-state")).toBe(raw);
  });

  it("says UNKNOWN when the publisher does not fold feed state", () => {
    renderHeader(aged(0, { state: "UNKNOWN" }));
    expect(screen.getByTestId("engine-state")).toHaveTextContent("UNKNOWN");
  });

  it("shows the blocked reason when there is one and no prettier detail", () => {
    renderHeader(aged(0, { state: "PAUSED", stateDetail: null, blockedReason: "paused" }));
    expect(screen.getByTestId("engine-state")).toHaveTextContent("PAUSED");
    expect(screen.getByText("paused")).toBeInTheDocument();
  });
});

describe("the version hash", () => {
  it("is the strategy version, shortened", () => {
    renderHeader(FRESH);
    const pill = screen.getByTestId("version-hash");
    expect(pill.textContent).toContain(FRESH.strategyVersion.slice(0, 8));
    expect(pill.textContent).toContain(FRESH.strategyVersion.slice(-4));
    expect(pill.textContent).not.toContain(FRESH.strategyVersion);
  });

  it("carries the full digest on hover", () => {
    renderHeader(FRESH);
    expect(screen.getByTestId("version-hash").querySelector("[title]")).toHaveAttribute(
      "title",
      FRESH.strategyVersion,
    );
  });

  it("says so when the version is not published", () => {
    renderHeader(aged(0, { strategyVersion: "" }));
    expect(screen.getByTestId("version-hash")).toHaveTextContent(/version unpublished/i);
  });
});

describe("the staleness banner", () => {
  it("is absent on a fresh payload", () => {
    renderHeader(aged(0));
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();
  });

  it(`is absent at exactly ${STALE_AFTER_SECONDS} seconds`, () => {
    renderHeader(aged(STALE_AFTER_SECONDS));
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();
  });

  it(`appears at ${STALE_AFTER_SECONDS + 1} seconds`, () => {
    renderHeader(aged(STALE_AFTER_SECONDS + 1));
    expect(screen.getByTestId("stale-banner")).toBeInTheDocument();
    expect(screen.getByTestId("stale-banner")).toHaveTextContent(/DATA STALE/i);
  });

  it("says the figures are out of date, not that the market stopped", () => {
    renderHeader(aged(90));
    expect(screen.getByTestId("stale-banner")).toHaveTextContent(
      /market has not necessarily stopped/i,
    );
  });

  it("is absent when the payload carries no timestamp at all", () => {
    renderHeader(aged(0, { generatedAt: null }));
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();
    expect(screen.getByTestId("payload-age")).toHaveTextContent(/no timestamp/i);
  });

  it("follows the clock rather than the render", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-03-10T12:00:00.000Z"));
    renderHeader({
      ...FRESH,
      generatedAt: "2026-03-10T11:59:45.000Z",
    });
    // 20s old: fresh.
    expect(screen.queryByTestId("stale-banner")).not.toBeInTheDocument();

    // 32s old: the timer's own tick re-renders, and the banner appears.
    act(() => {
      vi.setSystemTime(new Date("2026-03-10T12:00:32.000Z"));
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.getByTestId("stale-banner")).toBeInTheDocument();
  });
});

describe("with no projection at all", () => {
  it("does not crash and says the engine state is unknown", () => {
    renderHeader(null);
    expect(screen.getByTestId("engine-state")).toHaveTextContent("UNKNOWN");
    expect(screen.getByTestId("simulated-badge")).toBeInTheDocument();
    expect(screen.getByTestId("version-hash")).toHaveTextContent(/version unpublished/i);
  });
});
