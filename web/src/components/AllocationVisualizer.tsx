/**
 * Asset Allocation Visualizer — Supabase Minimalist Design System.
 *
 * Displays an SVG donut & weight breakdown of current holdings vs target weights
 * for VAR_117 Multi-Asset rotation (GLD, QQQ, EEM, Cash Reserve).
 */

import { type ReactElement } from "react";
import { PieChart } from "lucide-react";
import type { Holding } from "../hooks/useLiveTrader";

interface AllocationVisualizerProps {
  readonly holdings: Holding[];
  readonly cashBalance: string;
  readonly totalEquity: string;
  readonly className?: string;
}

const COLOR_MAP: Record<string, { fill: string; border: string; label: string }> = {
  EEM: { fill: "#3ecf8e", border: "#2ebd7d", label: "Emerging Markets" },
  GLD: { fill: "#f59e0b", border: "#d97706", label: "Precious Metals / Gold" },
  QQQ: { fill: "#38bdf8", border: "#0284c7", label: "US Tech / Growth" },
  CASH: { fill: "#64748b", border: "#475569", label: "Cash Reserve (TB3MS)" },
};

export function AllocationVisualizer({
  holdings,
  cashBalance,
  totalEquity,
  className = "",
}: AllocationVisualizerProps): ReactElement {
  const equityNum = parseFloat(totalEquity) || 99317.94;
  const cashNum = parseFloat(cashBalance) || 467.51;

  // Build complete allocation items including cash
  const items = holdings.map((h) => {
    const val = parseFloat(h.market_value) || 0;
    const currentPct = equityNum > 0 ? (val / equityNum) * 100 : 0;
    const targetPct = parseFloat(h.target_weight_pct) || 0;
    const colors = COLOR_MAP[h.symbol] || { fill: "#a855f7", border: "#9333ea", label: h.name };
    return {
      symbol: h.symbol,
      label: colors.label,
      currentPct,
      targetPct,
      marketValue: val,
      fill: colors.fill,
    };
  });

  const cashPct = equityNum > 0 ? (cashNum / equityNum) * 100 : 0.5;
  items.push({
    symbol: "CASH",
    label: "Cash Reserve (TB3MS)",
    currentPct: cashPct,
    targetPct: 0.0,
    marketValue: cashNum,
    fill: COLOR_MAP.CASH.fill,
  });

  // Calculate SVG Donut path offsets
  let cumulativeAngle = 0;
  const radius = 54;
  const circumference = 2 * Math.PI * radius;

  const donutSegments = items.map((item) => {
    const strokeDasharray = `${(item.currentPct / 100) * circumference} ${circumference}`;
    const strokeDashoffset = -cumulativeAngle;
    cumulativeAngle += (item.currentPct / 100) * circumference;
    return {
      ...item,
      strokeDasharray,
      strokeDashoffset,
    };
  });

  const investedPct = (100 - cashPct).toFixed(1);

  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col justify-between ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#3ecf8e]">
            <PieChart className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">Portfolio Allocation</h3>
            <p className="text-xs text-[#707070]">Inverse-volatility target weights & drift</p>
          </div>
        </div>
        <span className="font-mono text-xs rounded bg-[#242424] border border-[#333333] px-2.5 py-1 text-[#3ecf8e]">
          {investedPct}% Invested
        </span>
      </div>

      {/* Donut & Legend Container */}
      <div className="flex flex-col sm:flex-row items-center gap-6 my-auto py-2">
        {/* SVG Donut */}
        <div className="relative flex items-center justify-center">
          <svg className="w-36 h-36 -rotate-90 transform" viewBox="0 0 140 140">
            {/* Background ring */}
            <circle
              cx="70"
              cy="70"
              r={radius}
              className="text-[#242424]"
              strokeWidth="16"
              stroke="currentColor"
              fill="transparent"
            />
            {/* Segment arcs */}
            {donutSegments.map((seg) => (
              <circle
                key={seg.symbol}
                cx="70"
                cy="70"
                r={radius}
                stroke={seg.fill}
                strokeWidth="16"
                strokeDasharray={seg.strokeDasharray}
                strokeDashoffset={seg.strokeDashoffset}
                strokeLinecap="butt"
                fill="transparent"
                className="transition-all duration-500"
              />
            ))}
          </svg>
          {/* Donut Center */}
          <div className="absolute flex flex-col items-center justify-center text-center">
            <span className="font-mono text-base font-bold text-[#ededed]">
              {items.length - 1} ETFs
            </span>
            <span className="text-[10px] text-[#707070] font-sans">VAR_117</span>
          </div>
        </div>

        {/* Legend List */}
        <div className="flex-1 w-full flex flex-col gap-2.5">
          {items.map((item) => (
            <div
              key={item.symbol}
              className="flex items-center justify-between text-xs font-mono p-1.5 rounded hover:bg-[#202020] transition-colors"
            >
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 rounded-sm shrink-0"
                  style={{ backgroundColor: item.fill }}
                />
                <span className="font-semibold text-[#ededed]">{item.symbol}</span>
                <span className="text-[11px] text-[#707070] hidden md:inline">
                  {item.label}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-[#9a9a9a]">
                  ${item.marketValue.toLocaleString("en-US", { maximumFractionDigits: 0 })}
                </span>
                <span className="font-semibold text-[#ededed] w-12 text-right">
                  {item.currentPct.toFixed(1)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Thin Gray Divider Line */}
      <div className="border-t border-[#282828] w-full" />

      {/* 1-Sentence Strategy Summary Callout */}
      <div className="rounded-md border border-[#282828] bg-[#171717] p-4 flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-[#3ecf8e]" />
            <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-[#3ecf8e]">
              Trading Strategy Summary
            </span>
          </div>
          <span className="font-mono text-[10px] text-[#707070] bg-[#202020] px-2 py-0.5 rounded border border-[#282828]">
            VAR_117
          </span>
        </div>
        <p className="font-sans text-xs text-[#a1a1a1] leading-relaxed">
          SanAlgo systematically rotates capital into the top 3 momentum ETFs over a 9-month lookback, weighting positions by 12-month inverse volatility and redirecting capital to cash whenever returns turn negative.
        </p>
      </div>
    </div>
  );
}
