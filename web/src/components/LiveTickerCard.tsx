/**
 * Live Gold Ticker Header — Supabase Technical Minimalist Design.
 *
 * Designed according to DESIGN-supabase.md:
 * - Minimalist chrome, near-monochrome canvas (#1c1c1c on #121212)
 * - Solid typography: NO text gradients, NO neon effects
 * - Emerald (#3ecf8e) as the sole brand chromatic highlight
 * - Technical 6px/8px radii with clean 1px hairline borders
 */

import type { ReactElement } from "react";
import { ArrowDownRight, ArrowUpRight, CheckCircle2 } from "lucide-react";
import type { LiveSnapshot, LiveTick } from "../hooks/useLiveTrader";

interface LiveTickerCardProps {
  readonly snapshot: LiveSnapshot | null;
  readonly tick: LiveTick | null;
  readonly connected: boolean;
  readonly priceFlash: "up" | "down" | null;
}

export function LiveTickerCard({
  snapshot,
  tick,
  connected,
}: LiveTickerCardProps): ReactElement {
  const mid = tick ? tick.mid : snapshot?.mid ?? "----.--";
  const bid = tick ? tick.bid : snapshot?.bid ?? "----.--";
  const ask = tick ? tick.ask : snapshot?.ask ?? "----.--";
  const spreadTicks = tick ? tick.spread_ticks : snapshot?.spread_ticks ?? "--";

  const dailyChange = snapshot?.daily_change_pct ?? "+0.00";
  const isPositive = !dailyChange.startsWith("-");

  return (
    <div className="rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm">
      {/* Header Row */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-[#282828] pb-5">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-md bg-[#242424] border border-[#333333] font-mono font-bold text-[#3ecf8e] text-sm">
            Au
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-sans text-base font-semibold text-[#ededed]">
                XAU/USD
              </h2>
              <span className="rounded bg-[#242424] px-1.5 py-0.5 font-mono text-[11px] font-medium text-[#9a9a9a] border border-[#333333]">
                GOLD SPOT
              </span>
              <span className="rounded bg-[#242424] px-1.5 py-0.5 font-mono text-[11px] font-medium text-[#707070]">
                EXNESS MT5
              </span>
            </div>
            <p className="text-xs text-[#707070]">Institutional Zero-Spread Standard Feed</p>
          </div>
        </div>

        {/* Status Indicators */}
        <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
          {/* Liveness */}
          <div className="inline-flex items-center gap-2 rounded-md bg-[#202020] px-2.5 py-1 border border-[#2e2e2e]">
            <span
              className={`h-2 w-2 rounded-full ${
                connected ? "bg-[#3ecf8e]" : "bg-[#f43f5e] animate-pulse"
              }`}
            />
            <span className={connected ? "text-[#ededed]" : "text-[#f43f5e]"}>
              {connected ? "LIVE FEED" : "CONNECTING"}
            </span>
          </div>

          {/* Spread */}
          <div className="inline-flex items-center gap-1.5 rounded-md bg-[#202020] px-2.5 py-1 border border-[#2e2e2e] text-[#9a9a9a]">
            <span className="text-[#707070]">Spread:</span>
            <span className="font-semibold text-[#ededed]">{spreadTicks} ticks</span>
          </div>

          {/* Daily Change */}
          <div
            className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 border ${
              isPositive
                ? "bg-[#3ecf8e]/10 text-[#3ecf8e] border-[#3ecf8e]/30"
                : "bg-[#f43f5e]/10 text-[#f43f5e] border-[#f43f5e]/30"
            }`}
          >
            {isPositive ? (
              <ArrowUpRight className="h-3.5 w-3.5" />
            ) : (
              <ArrowDownRight className="h-3.5 w-3.5" />
            )}
            <span className="font-semibold">{dailyChange}%</span>
          </div>
        </div>
      </div>

      {/* Main Metric Row */}
      <div className="grid grid-cols-1 gap-6 pt-5 lg:grid-cols-12 lg:items-center">
        {/* Mid Price */}
        <div className="lg:col-span-5 flex flex-col gap-1">
          <span className="font-mono text-xs uppercase tracking-wider text-[#707070]">
            Market Mid Price (USD)
          </span>
          <div className="flex items-baseline gap-2">
            <span className="font-mono text-4xl sm:text-5xl font-semibold tracking-tight text-[#ffffff]">
              ${mid}
            </span>
          </div>
        </div>

        {/* Bid, Ask, High, Low Tiles */}
        <div className="lg:col-span-7 grid grid-cols-2 sm:grid-cols-4 gap-2.5">
          {/* Bid */}
          <div className="rounded-md bg-[#202020] p-2.5 border border-[#282828]">
            <p className="text-[11px] font-mono text-[#707070] uppercase">Bid</p>
            <p className="font-mono text-sm font-medium text-[#ededed] mt-0.5">${bid}</p>
          </div>

          {/* Ask */}
          <div className="rounded-md bg-[#202020] p-2.5 border border-[#282828]">
            <p className="text-[11px] font-mono text-[#707070] uppercase">Ask</p>
            <p className="font-mono text-sm font-medium text-[#ededed] mt-0.5">${ask}</p>
          </div>

          {/* 24h High */}
          <div className="rounded-md bg-[#202020] p-2.5 border border-[#282828]">
            <p className="text-[11px] font-mono text-[#707070] uppercase">24h High</p>
            <p className="font-mono text-sm font-medium text-[#9a9a9a] mt-0.5">
              ${snapshot?.daily_high ?? "--"}
            </p>
          </div>

          {/* 24h Low */}
          <div className="rounded-md bg-[#202020] p-2.5 border border-[#282828]">
            <p className="text-[11px] font-mono text-[#707070] uppercase">24h Low</p>
            <p className="font-mono text-sm font-medium text-[#9a9a9a] mt-0.5">
              ${snapshot?.daily_low ?? "--"}
            </p>
          </div>
        </div>
      </div>

      {/* Account Info Footer */}
      <div className="flex flex-wrap items-center justify-between gap-4 pt-4 mt-5 border-t border-[#282828] text-xs font-mono">
        <div className="flex items-center gap-6 text-[#9a9a9a]">
          <div>
            <span className="text-[#707070]">Balance: </span>
            <span className="text-[#ededed] font-medium">
              ${parseFloat(snapshot?.account_balance ?? "1000").toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </span>
          </div>
          <div>
            <span className="text-[#707070]">Equity: </span>
            <span className="text-[#3ecf8e] font-medium">
              ${parseFloat(snapshot?.account_equity ?? "1000").toLocaleString("en-US", { minimumFractionDigits: 2 })}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2 text-[#707070]">
          <CheckCircle2 className="h-3.5 w-3.5 text-[#3ecf8e]" />
          <span>MetaTrader 5 Automated Bridge Active</span>
        </div>
      </div>
    </div>
  );
}
