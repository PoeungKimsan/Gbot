/**
 * The N < 30 rule, and the numbers either side of it.
 *
 * `engine/metrics/performance.py` refuses to compute a bootstrap interval below 30 closed
 * trades. This suite pins the dashboard's half of that contract, which is the half a user
 * actually sees:
 *
 * * **Below the floor, no bounds are rendered.** Not greyed out, not `--`, not an interval
 *   over four trades with an asterisk. The string `INSUFFICIENT DATA (<30 TRADES)` appears
 *   in its place, in both panels.
 * * **At or above the floor, the bounds are rendered** and the explainer is gone.
 * * **The count is checked, not just the status string.** A payload that claims `COMPLETE`
 *   with twelve trades is treated as insufficient, because the point of the floor is that a
 *   producer can be wrong.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MINIMUM_SAMPLE_SIZE } from "../api/contract";
import { intervalBounds } from "../api/client";
import { METRICS_COMPLETE, METRICS_INSUFFICIENT } from "../api/fixtures";
import { ExpectancyIsland, PerformancePanel } from "./PerformancePanel";

/** A payload that claims to be complete but is not. */
const LYING_COMPLETE = {
  ...METRICS_COMPLETE,
  status: "COMPLETE" as const,
  tradeCount: 12,
  confidenceInterval: { lower: -0.5, upper: 0.9 },
};

const EMPTY = {
  ...METRICS_INSUFFICIENT,
  status: "INSUFFICIENT_DATA" as const,
  tradeCount: 0,
  realizedPnl: 0,
  maxDrawdown: 0,
  winRate: null,
  profitFactor: null,
  expectancyR: null,
  confidenceInterval: null,
};

describe("the threshold", () => {
  it("is 30 trades, mirroring the engine's floor", () => {
    expect(MINIMUM_SAMPLE_SIZE).toBe(30);
  });

  it("an interval below the floor is not reportable", () => {
    expect(intervalBounds(METRICS_INSUFFICIENT)).toBeNull();
  });

  it("an interval at the floor is reportable", () => {
    expect(intervalBounds({ ...METRICS_COMPLETE, tradeCount: 30 })).not.toBeNull();
  });

  it("a payload claiming COMPLETE below the floor is not believed", () => {
    expect(intervalBounds(LYING_COMPLETE)).toBeNull();
  });
});

describe("the hero performance panel below the floor", () => {
  it("says INSUFFICIENT DATA (<30 TRADES) instead of bounds", () => {
    render(<PerformancePanel metrics={METRICS_INSUFFICIENT} />);
    expect(screen.getByTestId("insufficient-data")).toHaveTextContent(
      /INSUFFICIENT DATA \(<30 TRADES\)/i,
    );
  });

  it("renders no confidence bounds at all", () => {
    render(<PerformancePanel metrics={METRICS_INSUFFICIENT} />);
    expect(screen.queryByTestId("confidence-bounds")).not.toBeInTheDocument();
  });

  it("still shows the point estimates", () => {
    render(<PerformancePanel metrics={METRICS_INSUFFICIENT} />);
    expect(screen.getByTestId("stat-pnl")).toHaveTextContent(
      METRICS_INSUFFICIENT.realizedPnl.toFixed(2),
    );
    expect(screen.getByTestId("stat-expectancy")).toHaveTextContent(
      (METRICS_INSUFFICIENT.expectancyR ?? 0).toFixed(2),
    );
    expect(screen.getByTestId("stat-drawdown")).toHaveTextContent(
      METRICS_INSUFFICIENT.maxDrawdown.toFixed(2),
    );
    expect(screen.getByTestId("stat-count")).toHaveTextContent("12");
  });

  it("explains why there are no bounds", () => {
    render(<PerformancePanel metrics={METRICS_INSUFFICIENT} />);
    expect(screen.getByTestId("insufficient-explainer")).toHaveTextContent(/bootstrap over 12/);
  });

  it("flags the trade count as below the floor", () => {
    render(<PerformancePanel metrics={METRICS_INSUFFICIENT} />);
    expect(screen.getByTestId("sample-status")).toHaveTextContent(/below floor/);
  });
});

describe("the expectancy island below the floor", () => {
  it("says INSUFFICIENT DATA (<30 TRADES) instead of bounds", () => {
    render(<ExpectancyIsland metrics={METRICS_INSUFFICIENT} />);
    expect(screen.getByTestId("island-insufficient")).toHaveTextContent(
      /INSUFFICIENT DATA \(<30 TRADES\)/i,
    );
  });

  it("renders no bounds", () => {
    render(<ExpectancyIsland metrics={METRICS_INSUFFICIENT} />);
    expect(screen.queryByTestId("island-bounds")).not.toBeInTheDocument();
  });

  it("still shows the headline expectancy and the count", () => {
    render(<ExpectancyIsland metrics={METRICS_INSUFFICIENT} />);
    const expected = `${(METRICS_INSUFFICIENT.expectancyR ?? 0).toFixed(2)} R`;
    expect(screen.getByTestId("island-expectancy")).toHaveTextContent(expected);
    expect(screen.getByTestId("island-count")).toHaveTextContent("12");
  });
});

describe("at and above the floor", () => {
  it("the hero panel renders the bounds", () => {
    render(<PerformancePanel metrics={METRICS_COMPLETE} />);
    const bounds = screen.getByTestId("confidence-bounds");
    expect(bounds).toHaveTextContent(/[+-]\d\.\d\d R/);
    expect(bounds.textContent).toContain("→");
  });

  it("the hero panel renders no insufficient-data notice", () => {
    render(<PerformancePanel metrics={METRICS_COMPLETE} />);
    expect(screen.queryByTestId("insufficient-data")).not.toBeInTheDocument();
    expect(screen.queryByTestId("insufficient-explainer")).not.toBeInTheDocument();
  });

  it("the island renders the bounds", () => {
    render(<ExpectancyIsland metrics={METRICS_COMPLETE} />);
    expect(screen.getByTestId("island-bounds")).toHaveTextContent("→");
  });

  it("the island renders no insufficient-data notice", () => {
    render(<ExpectancyIsland metrics={METRICS_COMPLETE} />);
    expect(screen.queryByTestId("island-insufficient")).not.toBeInTheDocument();
  });

  it("the island marks the interval as bounded", () => {
    render(<ExpectancyIsland metrics={METRICS_COMPLETE} />);
    expect(screen.getByTestId("island-status")).toHaveTextContent(/bounded/i);
  });
});

describe("the empty run", () => {
  it("a run with no trades shows the insufficient notice, not an error", () => {
    render(<PerformancePanel metrics={EMPTY} />);
    expect(screen.getByTestId("insufficient-data")).toHaveTextContent(
      /INSUFFICIENT DATA \(<30 TRADES\)/i,
    );
    expect(screen.getByTestId("stat-count")).toHaveTextContent("0");
  });

  it("a run with no trades has no expectancy to print", () => {
    render(<PerformancePanel metrics={EMPTY} />);
    expect(screen.getByTestId("stat-expectancy")).toHaveTextContent("--");
  });
});

describe("the lying payload", () => {
  it("a COMPLETE status with twelve trades still hides the bounds", () => {
    render(<PerformancePanel metrics={LYING_COMPLETE} />);
    expect(screen.queryByTestId("confidence-bounds")).not.toBeInTheDocument();
    expect(screen.getByTestId("insufficient-data")).toBeInTheDocument();
  });

  it("and it does so in the island too", () => {
    render(<ExpectancyIsland metrics={LYING_COMPLETE} />);
    expect(screen.queryByTestId("island-bounds")).not.toBeInTheDocument();
    expect(screen.getByTestId("island-insufficient")).toBeInTheDocument();
  });
});

describe("when metrics have not been published at all", () => {
  it("the hero panel says so instead of showing zeros", () => {
    render(<PerformancePanel metrics={null} />);
    const panel = screen.getByTestId("performance-panel");
    expect(within(panel).getByTestId("not-published")).toBeInTheDocument();
    expect(within(panel).queryByTestId("stat-pnl")).not.toBeInTheDocument();
  });

  it("the island says so too", () => {
    render(<ExpectancyIsland metrics={null} />);
    expect(within(screen.getByTestId("expectancy-island")).getByTestId("not-published")).toBeInTheDocument();
  });
});
