/**
 * Active Holdings Table — Supabase Technical Minimalist Design.
 *
 * Displays portfolio ETF positions with current allocation, target weight,
 * drift percentage, and unrealized profit & loss.
 */

import type { ReactElement } from "react";
import { Layers } from "lucide-react";
import type { Holding } from "../hooks/useLiveTrader";

interface HoldingsTableProps {
  readonly holdings: Holding[];
  readonly className?: string;
}

export function HoldingsTable({ holdings, className = "" }: HoldingsTableProps): ReactElement {
  return (
    <div className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col gap-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#ededed]">
            <Layers className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">Current Portfolio Holdings</h3>
            <p className="text-xs text-[#707070]">Live ETF positions & systematic target weights</p>
          </div>
        </div>
        <span className="font-mono text-xs rounded bg-[#242424] border border-[#333333] px-2.5 py-1 text-[#9a9a9a]">
          {holdings.length} Assets
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left font-sans text-xs">
          <thead>
            <tr className="border-b border-[#282828] text-[#707070]">
              <th className="pb-3 font-medium">Asset</th>
              <th className="pb-3 font-medium text-right">Shares</th>
              <th className="pb-3 font-medium text-right">Price</th>
              <th className="pb-3 font-medium text-right">Market Value</th>
              <th className="pb-3 font-medium text-right">Current %</th>
              <th className="pb-3 font-medium text-right">Target %</th>
              <th className="pb-3 font-medium text-right">Drift %</th>
              <th className="pb-3 font-medium text-right">Unrealized P&L</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#242424] font-mono">
            {holdings.length === 0 ? (
              <tr>
                <td colSpan={8} className="py-8 text-center text-[#707070] font-sans">
                  No active ETF positions held. Capital is currently 100% in Cash Reserve (TB3MS).
                </td>
              </tr>
            ) : (
              holdings.map((h) => {
                const drift = parseFloat(h.drift_pct);
                const pl = parseFloat(h.unrealized_pl);
                return (
                  <tr key={h.symbol} className="hover:bg-[#202020] transition-colors">
                    <td className="py-3">
                      <div className="font-semibold text-[#ededed]">{h.symbol}</div>
                      <div className="font-sans text-[11px] text-[#707070]">{h.name}</div>
                    </td>
                    <td className="py-3 text-right text-[#ededed]">{parseFloat(h.qty).toFixed(4)}</td>
                    <td className="py-3 text-right text-[#9a9a9a]">${h.current_price}</td>
                    <td className="py-3 text-right text-[#ededed] font-medium">${h.market_value}</td>
                    <td className="py-3 text-right text-[#ededed]">{h.current_weight_pct}%</td>
                    <td className="py-3 text-right text-[#3ecf8e] font-semibold">{h.target_weight_pct}%</td>
                    <td className={`py-3 text-right ${Math.abs(drift) > 2 ? "text-[#f59e0b]" : "text-[#707070]"}`}>
                      {drift > 0 ? `+${h.drift_pct}%` : `${h.drift_pct}%`}
                    </td>
                    <td className={`py-3 text-right font-medium ${pl >= 0 ? "text-[#3ecf8e]" : "text-[#f43f5e]"}`}>
                      {pl >= 0 ? `+$${pl.toFixed(2)}` : `-$${Math.abs(pl).toFixed(2)}`}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
