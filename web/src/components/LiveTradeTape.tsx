/**
 * Live Trade History Tape — Supabase Technical Minimalist Design.
 *
 * Designed according to DESIGN-supabase.md:
 * - Clean developer-tool table layout
 * - Monospace data alignment, 1px subtle hairlines
 * - Emerald (#3ecf8e) and Red (#f43f5e) side badges
 */

import type { ReactElement } from "react";
import { ArrowDownRight, ArrowUpRight, History } from "lucide-react";
import type { LiveTrade } from "../hooks/useLiveTrader";

interface LiveTradeTapeProps {
  readonly trades: readonly LiveTrade[];
  readonly className?: string;
}

export function LiveTradeTape({
  trades,
  className = "",
}: LiveTradeTapeProps): ReactElement {
  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col gap-4 ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#ededed]">
            <History className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">Trade History</h3>
            <p className="text-xs text-[#707070]">Verified order fills & closed positions</p>
          </div>
        </div>

        <span className="rounded bg-[#202020] px-2 py-1 font-mono text-xs text-[#707070] border border-[#2e2e2e]">
          {trades.length} Executed
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        {trades.length === 0 ? (
          <div className="rounded-md border border-dashed border-[#282828] bg-[#171717] p-6 text-center text-[#707070] flex flex-col items-center gap-1 font-mono text-xs">
            <span className="text-[#9a9a9a]">No trades executed yet today</span>
            <span className="text-[11px] text-[#707070]">
              Orders will log here automatically when a portfolio rebalance fills.
            </span>
          </div>
        ) : (
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="border-b border-[#282828] text-[11px] text-[#707070] uppercase">
                <th className="pb-3 font-normal">Order</th>
                <th className="pb-3 font-normal">Strategy</th>
                <th className="pb-3 font-normal">Side</th>
                <th className="pb-3 font-normal">Lots</th>
                <th className="pb-3 font-normal">Entry</th>
                <th className="pb-3 font-normal">Stop Loss</th>
                <th className="pb-3 font-normal">Take Profit</th>
                <th className="pb-3 font-normal text-right">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#242424]">
              {trades.map((t) => {
                const isBuy = t.side === "BUY";
                return (
                  <tr key={t.id} className="hover:bg-[#202020]/60 transition-colors">
                    <td className="py-2.5 text-[#9a9a9a]">#{t.id}</td>
                    <td className="py-2.5">
                      <span className="rounded bg-[#242424] px-1.5 py-0.5 text-[11px] text-[#ededed] border border-[#333333]">
                        {t.strategy || "VAR_117"}
                      </span>
                    </td>
                    <td className="py-2.5">
                      <span
                        className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium border ${
                          isBuy
                            ? "bg-[#3ecf8e]/10 text-[#3ecf8e] border-[#3ecf8e]/30"
                            : "bg-[#f43f5e]/10 text-[#f43f5e] border-[#f43f5e]/30"
                        }`}
                      >
                        {isBuy ? (
                          <ArrowUpRight className="h-3 w-3" />
                        ) : (
                          <ArrowDownRight className="h-3 w-3" />
                        )}
                        {t.side}
                      </span>
                    </td>
                    <td className="py-2.5 text-[#ededed]">{t.volume}L</td>
                    <td className="py-2.5 text-[#ededed] font-medium">${t.entry}</td>
                    <td className="py-2.5 text-[#f43f5e]">${t.sl ?? "--"}</td>
                    <td className="py-2.5 text-[#3ecf8e]">${t.tp ?? "--"}</td>
                    <td className="py-2.5 text-right text-[#707070]">
                      {new Date(t.time).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                        second: "2-digit",
                      })}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
