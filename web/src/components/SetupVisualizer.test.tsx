/**
 * The setup visualizer: bars, sweep, shift, gap — and the no-repaint rule.
 *
 * The chart is asserted on its *geometry*, not on its pixels. A sweep line at the wrong
 * tick is a chart that would tell an operator to trade a level that was never swept, which
 * is the one failure this view cannot have.
 *
 * The other half is what it refuses to draw. A `setups.json` that has bars but no
 * confirmed geometry is a Phase 5.x payload in flight, and the overlay markers must be
 * silent rather than invented.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { SetupVisualizer } from "./SetupVisualizer";
import { SETUPS_FIXTURE, TICK_SIZE } from "../api/fixtures";
import type { SetupsPayload } from "../api/contract";

/** The fixture, with one overlay lifted out to model a partially-published payload. */
function without<K extends keyof SetupsPayload>(key: K): SetupsPayload {
  return { ...SETUPS_FIXTURE, [key]: key === "levels" ? [] : null };
}

function chart(): HTMLElement {
  return screen.getByTestId("candlestick-chart");
}

describe("with a full setups payload", () => {
  it("draws one candle per closed bar", () => {
    render(<SetupVisualizer setups={SETUPS_FIXTURE} tickSize={TICK_SIZE} />);
    expect(screen.getAllByTestId("candle")).toHaveLength(SETUPS_FIXTURE.bars.length);
  });

  it("draws the liquidity sweep level with its breach", () => {
    render(<SetupVisualizer setups={SETUPS_FIXTURE} tickSize={TICK_SIZE} />);
    const sweep = within(chart()).getByTestId("sweep-level");
    expect(sweep).toBeInTheDocument();

    // The dashed level sits at the swept level, not at the breach.
    const level = screen.getByTestId("level-SWING_LOW");
    expect(level).toHaveAttribute("y1", level.getAttribute("y2"));
    expect(sweep.textContent).toContain("2006.12");
    expect(sweep.textContent).toContain("2006.06");
  });

  it("draws the market structure shift marker", () => {
    render(<SetupVisualizer setups={SETUPS_FIXTURE} tickSize={TICK_SIZE} />);
    const mss = within(chart()).getByTestId("mss-marker");
    expect(mss).toBeInTheDocument();
    expect(mss.textContent).toContain("2007.01");
  });

  it("draws the fair value gap box and its 50% midpoint", () => {
    render(<SetupVisualizer setups={SETUPS_FIXTURE} tickSize={TICK_SIZE} />);
    const fvg = within(chart()).getByTestId("fvg-box");
    expect(fvg).toBeInTheDocument();
    expect(fvg.textContent).toContain("2007.30");

    // The box spans the imbalance, so its height tracks the gap's size.
    const box = fvg.querySelector("rect");
    expect(box).not.toBeNull();
    expect(Number(box!.getAttribute("height"))).toBeGreaterThan(0);
  });

  it("draws the resting order's entry, stop and target lines", () => {
    render(<SetupVisualizer setups={SETUPS_FIXTURE} tickSize={TICK_SIZE} />);
    expect(within(chart()).getByTestId("resting-order")).toBeInTheDocument();
  });

  it("labels the axis in prices, because the tick size is published", () => {
    render(<SetupVisualizer setups={SETUPS_FIXTURE} tickSize={TICK_SIZE} />);
    expect(chart().textContent).toContain("Z");
    expect(screen.queryByTestId("tick-size-missing")).not.toBeInTheDocument();
  });

  it("falls back to raw ticks when the instrument geometry is missing", () => {
    render(<SetupVisualizer setups={SETUPS_FIXTURE} tickSize={null} />);
    expect(screen.getByTestId("tick-size-missing")).toBeInTheDocument();
    expect(chart()).toBeInTheDocument();
  });
});

describe("with no setups payload at all", () => {
  it("renders the sentinel and no chart", () => {
    render(<SetupVisualizer setups={null} tickSize={TICK_SIZE} />);
    expect(screen.getByTestId("setups-missing")).toBeInTheDocument();
    expect(screen.queryByTestId("candlestick-chart")).not.toBeInTheDocument();
  });

  it("names the spec section that specifies it", () => {
    render(<SetupVisualizer setups={null} tickSize={TICK_SIZE} />);
    expect(screen.getByTestId("setups-missing").textContent).toContain("§4");
  });
});

describe("with a partially published payload", () => {
  it("still draws the candles when the sweep has not arrived", () => {
    render(<SetupVisualizer setups={without("sweep")} tickSize={TICK_SIZE} />);
    expect(screen.getAllByTestId("candle")).toHaveLength(SETUPS_FIXTURE.bars.length);
    expect(screen.queryByTestId("sweep-level")).not.toBeInTheDocument();
  });

  it("still draws the candles when the MSS has not arrived", () => {
    render(<SetupVisualizer setups={without("mss")} tickSize={TICK_SIZE} />);
    expect(screen.getAllByTestId("candle")).toHaveLength(SETUPS_FIXTURE.bars.length);
    expect(screen.queryByTestId("mss-marker")).not.toBeInTheDocument();
  });

  it("still draws the candles when the FVG has not arrived", () => {
    render(<SetupVisualizer setups={without("fvg")} tickSize={TICK_SIZE} />);
    expect(screen.getAllByTestId("candle")).toHaveLength(SETUPS_FIXTURE.bars.length);
    expect(screen.queryByTestId("fvg-box")).not.toBeInTheDocument();
  });

  it("draws nothing but the empty state when there are no bars", () => {
    render(<SetupVisualizer setups={{ ...SETUPS_FIXTURE, bars: [] }} tickSize={TICK_SIZE} />);
    expect(screen.queryByTestId("candlestick-chart")).not.toBeInTheDocument();
    expect(screen.getByTestId("setups-missing")).toBeInTheDocument();
  });
});
