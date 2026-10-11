/**
 * Live Position & P&L — Supabase Technical Minimalist Design.
 *
 * Designed according to DESIGN-supabase.md:
 * - Clean near-monochrome cards (#1c1c1c on #121212)
 * - Emerald (#3ecf8e) for positive metrics, solid white (#ededed) typography
 * - Zero neons, zero gradients, 6px/8px technical radii
 */

import type { ReactElement } from "react";
import { DollarSign, Layers } from "lucide-react";
import type { LivePosition, LiveSnapshot } from "../hooks/useLiveTrader";

interface LivePositionPnLProps {
  readonly snapshot: LiveSnapshot | null;
  readonly className?: string;
}

export function LivePositionPnL({
  snapshot,
  className = "",
}: LivePositionPnLProps): ReactElement {
  const unrealized = snapshot?.unrealized_pnl ?? "+0.00";
  const realized = snapshot?.realized_pnl_today ?? "+0.00";
  const isUnrealizedPositive = !unrealized.startsWith("-");
  const isRealizedPositive = !realized.startsWith("-");

  const positions: LivePosition[] = snapshot?.open_positions ?? [];

  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col gap-5 ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#ededed]">
            <DollarSign className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">P&L & Positions</h3>
            <p className="text-xs text-[#707070]">Real-time execution telemetry</p>
          </div>
        </div>

        <span className="rounded bg-[#202020] px-2 py-1 font-mono text-xs text-[#707070] border border-[#2e2e2e]">
          {positions.length} Active {positions.length === 1 ? "Trade" : "Trades"}
        </span>
      </div>

      {/* KPI Tiles */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {/* Unrealized PnL */}
        <div className="rounded-md bg-[#202020] p-4 border border-[#282828]">
          <span className="font-mono text-xs uppercase text-[#707070]">Unrealized P&L</span>
          <p
            className={`font-mono text-2xl font-semibold mt-1 ${
              isUnrealizedPositive ? "text-[#3ecf8e]" : "text-[#f43f5e]"
            }`}
          >
            ${unrealized}
          </p>
          <p className="text-xs text-[#707070] mt-0.5">Floating market value</p>
        </div>

        {/* Realized PnL */}
        <div className="rounded-md bg-[#202020] p-4 border border-[#282828]">
          <span className="font-mono text-xs uppercase text-[#707070]">Realized Today</span>
          <p
            className={`font-mono text-2xl font-semibold mt-1 ${
              isRealizedPositive ? "text-[#ededed]" : "text-[#f43f5e]"
            }`}
          >
            ${realized}
          </p>
          <p className="text-xs text-[#707070] mt-0.5">Closed trades performance</p>
        </div>
      </div>

      {/* Active Positions */}
      <div className="flex flex-col gap-2.5 pt-1">
        <span className="font-mono text-xs uppercase tracking-wider text-[#707070]">
          Active Positions
        </span>

        {positions.length === 0 ? (
          <div className="rounded-md border border-dashed border-[#282828] bg-[#171717] p-6 text-center text-[#707070] flex flex-col items-center gap-1 font-mono text-xs">
            <Layers className="h-5 w-5 text-[#333333]" />
            <span className="text-[#9a9a9a]">No open positions</span>
            <span className="text-[11px] text-[#707070]">
              Scanning for ICT Judas Swing & Silver Bullet setups
            </span>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {positions.map((p) => (
              <div
                key={p.ticket}
                className="rounded-md bg-[#202020] border border-[#282828] p-3 flex items-center justify-between text-xs font-mono"
              >
                <div className="flex items-center gap-2.5">
                  <span
                    className={`px-1.5 py-0.5 rounded text-[11px] font-semibold border ${
                      p.side === "BUY"
                        ? "bg-[#3ecf8e]/10 text-[#3ecf8e] border-[#3ecf8e]/30"
                        : "bg-[#f43f5e]/10 text-[#f43f5e] border-[#f43f5e]/30"
                    }`}
                  >
                    {p.side} {p.volume}L
                  </span>
                  <div>
                    <span className="text-[#ededed]">#{p.ticket}</span>
                    <p className="text-[11px] text-[#707070]">Open: ${p.open_price}</p>
                  </div>
                </div>

                <div className="text-right">
                  <p
                    className={`font-medium ${
                      parseFloat(p.profit) >= 0 ? "text-[#3ecf8e]" : "text-[#f43f5e]"
                    }`}
                  >
                    {parseFloat(p.profit) >= 0 ? `+$${p.profit}` : `-$${Math.abs(parseFloat(p.profit))}`}
                  </p>
                  <p className="text-[11px] text-[#707070]">Current: ${p.current_price}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
