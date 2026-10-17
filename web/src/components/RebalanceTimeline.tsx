/**
 * Rebalance Timeline & Order Tape — Supabase Technical Minimalist Design.
 *
 * Displays systematic rebalance execution logs, order fills, and
 * the hash-chained verification commit.
 */

import type { ReactElement } from "react";
import { AlertCircle, Ban, Calendar, CheckCircle2, Clock, History } from "lucide-react";
import type { RebalanceInfo, RebalanceOrder } from "../hooks/useLiveTrader";

interface RebalanceTimelineProps {
  readonly rebalanceInfo: RebalanceInfo | null;
  readonly recentOrders: RebalanceOrder[];
  readonly className?: string;
}

export function RebalanceTimeline({
  rebalanceInfo,
  recentOrders,
  className = "",
}: RebalanceTimelineProps): ReactElement {
  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col justify-between gap-4 ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#ededed]">
            <History className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">Rebalance Timeline & Orders</h3>
            <p className="text-xs text-[#707070]">Monthly execution audit & delta trade history</p>
          </div>
        </div>
      </div>

      {/* Schedule Banner */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="rounded bg-[#202020] border border-[#282828] px-3.5 py-2.5 flex items-center gap-3">
          <div className="p-1.5 rounded bg-[#242424] text-[#3ecf8e]">
            <Calendar className="h-4 w-4" />
          </div>
          <div>
            <div className="text-[10px] text-[#707070] uppercase tracking-wider font-sans">Next Rebalance Date</div>
            <div className="font-mono text-xs font-semibold text-[#ededed]">
              {rebalanceInfo?.next_rebalance_date || "First Trading Day of Month"}
            </div>
          </div>
        </div>

        <div className="rounded bg-[#202020] border border-[#282828] px-3.5 py-2.5 flex items-center gap-3">
          <div className="p-1.5 rounded bg-[#242424] text-[#9a9a9a]">
            <Clock className="h-4 w-4" />
          </div>
          <div>
            <div className="text-[10px] text-[#707070] uppercase tracking-wider font-sans">Implementation Delay</div>
            <div className="font-mono text-xs font-semibold text-[#ededed]">
              1 Trading Day (Market Close)
            </div>
          </div>
        </div>
      </div>

      {/* Hash Commitment */}
      {rebalanceInfo?.signal_hash && (
        <div className="rounded bg-[#171717] border border-[#282828] p-2.5 font-mono text-[11px] text-[#707070] flex items-center gap-2">
          <span className="text-[#3ecf8e]">●</span>
          <span className="shrink-0 text-[#9a9a9a]">Signal Hash:</span>
          <span className="truncate text-[#555555]">{rebalanceInfo.signal_hash}</span>
        </div>
      )}

      {/* Order Execution History */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-medium text-[#9a9a9a] uppercase tracking-wider font-sans">
            Execution Order History
          </div>
          <div className="text-[11px] font-sans text-[#707070]">
            US Market Hours: 9:30 AM - 4:00 PM ET
          </div>
        </div>
        <div className="overflow-x-auto max-h-[175px] overflow-y-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead>
              <tr className="border-b border-[#282828] text-[#707070]">
                <th className="pb-2 font-medium">Time</th>
                <th className="pb-2 font-medium">Asset</th>
                <th className="pb-2 font-medium text-center">Side</th>
                <th className="pb-2 font-medium text-right">Shares</th>
                <th className="pb-2 font-medium text-right">Price</th>
                <th className="pb-2 font-medium text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#242424]">
              {recentOrders.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-[#707070] font-sans">
                    No rebalance orders executed yet this period.
                  </td>
                </tr>
              ) : (
                recentOrders.map((ord, idx) => {
                  const isBuy = ord.side.toLowerCase() === "buy";
                  const timeStr = ord.created_at ? new Date(ord.created_at).toLocaleTimeString() : "-";
                  const rawStatus = (ord.status || "").toLowerCase();
                  const isFilled = rawStatus === "filled";
                  const isPending = rawStatus === "new" || rawStatus === "pending_new" || rawStatus === "accepted";
                  const isCanceled = rawStatus === "canceled" || rawStatus === "cancelled";

                  return (
                    <tr
                      key={ord.id || idx}
                      className={`hover:bg-[#202020] transition-colors ${
                        isCanceled ? "opacity-50" : ""
                      }`}
                    >
                      <td className="py-2.5 text-[#707070]">{timeStr}</td>
                      <td className="py-2.5 font-bold text-[#ededed]">{ord.symbol}</td>
                      <td className="py-2.5 text-center">
                        <span
                          className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                            isBuy
                              ? "bg-[#162920] border border-[#1f4a33] text-[#3ecf8e]"
                              : "bg-[#331118] border border-[#591622] text-[#f43f5e]"
                          }`}
                        >
                          {ord.side.toUpperCase()}
                        </span>
                      </td>
                      <td className="py-2.5 text-right text-[#ededed]">
                        {parseFloat(ord.qty).toFixed(4)}
                      </td>
                      <td className="py-2.5 text-right text-[#9a9a9a]">
                        ${ord.filled_avg_price || "MKT"}
                      </td>
                      <td className="py-2.5 text-center">
                        {isFilled ? (
                          <span className="inline-flex items-center gap-1 rounded bg-[#162920] border border-[#1f4a33] px-1.5 py-0.5 text-[10px] font-semibold text-[#3ecf8e]">
                            <CheckCircle2 className="h-3 w-3" />
                            <span>FILLED</span>
                          </span>
                        ) : isPending ? (
                          <span
                            className="inline-flex items-center gap-1 rounded bg-[#2b220e] border border-[#4d3a14] px-1.5 py-0.5 text-[10px] font-semibold text-[#facc15]"
                            title="Queued in order book. Will automatically execute when market opens at 9:30 AM ET."
                          >
                            <Clock className="h-3 w-3 animate-pulse" />
                            <span>PENDING (Pre-Market)</span>
                          </span>
                        ) : isCanceled ? (
                          <span className="inline-flex items-center gap-1 rounded bg-[#242424] border border-[#333333] px-1.5 py-0.5 text-[10px] font-medium text-[#707070]">
                            <Ban className="h-3 w-3" />
                            <span>CANCELED</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 rounded bg-[#331118] border border-[#591622] px-1.5 py-0.5 text-[10px] font-semibold text-[#f43f5e]">
                            <AlertCircle className="h-3 w-3" />
                            <span>{rawStatus.toUpperCase()}</span>
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
