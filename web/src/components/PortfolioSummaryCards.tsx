/**
 * Portfolio Summary Cards — Supabase Minimalist Design System.
 *
 * Displays Total NAV, Cash Balance & Allocation, Portfolio Drawdown,
 * and Kill-Switch status in crisp monochrome with emerald (#3ecf8e) accents.
 */

import type { ReactElement } from "react";
import { DollarSign, ShieldAlert, ShieldCheck, Wallet, PieChart } from "lucide-react";
import type { LiveSnapshot } from "../hooks/useLiveTrader";

interface PortfolioSummaryCardsProps {
  readonly snapshot: LiveSnapshot | null;
}

export function PortfolioSummaryCards({ snapshot }: PortfolioSummaryCardsProps): ReactElement {
  const equity = snapshot?.account_equity ? parseFloat(snapshot.account_equity).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "100,000.00";
  const cash = snapshot?.cash_balance ? parseFloat(snapshot.cash_balance).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "100,000.00";
  const invested = snapshot?.invested_value ? parseFloat(snapshot.invested_value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "0.00";
  const unrealized = snapshot?.unrealized_pnl || "+0.00";
  const ddNum = parseFloat(snapshot?.drawdown_pct || "0");
  const drawdown = `${(isNaN(ddNum) ? 0 : ddNum).toFixed(2)}%`;
  const isTripped = snapshot?.killswitch?.is_tripped ?? false;
  const killswitchState = snapshot?.killswitch?.state ?? "ARMED";

  // Cash allocation %
  const totalEq = parseFloat(snapshot?.account_equity || "100000");
  const cashVal = parseFloat(snapshot?.cash_balance || "100000");
  const cashPct = totalEq > 0 ? ((cashVal / totalEq) * 100).toFixed(1) : "100.0";

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {/* 1. Total Portfolio NAV */}
      <div className="rounded-lg border border-[#282828] bg-[#1c1c1c] p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <span className="font-sans text-xs font-medium uppercase tracking-wider text-[#9a9a9a]">
            Portfolio NAV
          </span>
          <div className="flex h-7 w-7 items-center justify-center rounded bg-[#242424] border border-[#333333] text-[#3ecf8e]">
            <Wallet className="h-4 w-4" strokeWidth={1.5} />
          </div>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="font-mono text-2xl font-bold tracking-tight text-[#ededed]">
            ${equity}
          </span>
        </div>
        <div className="mt-2 flex items-center gap-1.5 font-mono text-xs">
          <span className="text-[#707070]">Unrealized:</span>
          <span className={unrealized.startsWith("+") ? "text-[#3ecf8e]" : "text-[#f43f5e]"}>
            ${unrealized}
          </span>
        </div>
      </div>

      {/* 2. Cash Balance & Allocation */}
      <div className="rounded-lg border border-[#282828] bg-[#1c1c1c] p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <span className="font-sans text-xs font-medium uppercase tracking-wider text-[#9a9a9a]">
            Cash Reserve (TB3MS)
          </span>
          <div className="flex h-7 w-7 items-center justify-center rounded bg-[#242424] border border-[#333333] text-[#ededed]">
            <DollarSign className="h-4 w-4" strokeWidth={1.5} />
          </div>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="font-mono text-2xl font-bold tracking-tight text-[#ededed]">
            ${cash}
          </span>
          <span className="font-mono text-xs text-[#9a9a9a]">({cashPct}%)</span>
        </div>
        <div className="mt-2 flex items-center gap-1.5 font-mono text-xs text-[#707070]">
          <span>Invested:</span>
          <span className="text-[#ededed]">${invested}</span>
        </div>
      </div>

      {/* 3. Portfolio Allocation Status */}
      <div className="rounded-lg border border-[#282828] bg-[#1c1c1c] p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <span className="font-sans text-xs font-medium uppercase tracking-wider text-[#9a9a9a]">
            Active Holdings
          </span>
          <div className="flex h-7 w-7 items-center justify-center rounded bg-[#242424] border border-[#333333] text-[#ededed]">
            <PieChart className="h-4 w-4" strokeWidth={1.5} />
          </div>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="font-mono text-2xl font-bold tracking-tight text-[#ededed]">
            {snapshot?.holdings?.length ?? 0}
          </span>
          <span className="text-xs text-[#707070]">/ 3 Target ETFs</span>
        </div>
        <div className="mt-2 flex items-center gap-1.5 font-mono text-xs text-[#707070]">
          <span>Top Assets:</span>
          <span className="text-[#3ecf8e]">
            {snapshot?.rebalance_info?.top_assets?.join(", ") || "None"}
          </span>
        </div>
      </div>

      {/* 4. Drawdown & Kill-Switch */}
      <div className="rounded-lg border border-[#282828] bg-[#1c1c1c] p-5 shadow-sm">
        <div className="flex items-center justify-between">
          <span className="font-sans text-xs font-medium uppercase tracking-wider text-[#9a9a9a]">
            Risk & Kill-Switch
          </span>
          <div
            className={`flex h-7 w-7 items-center justify-center rounded border ${
              isTripped
                ? "bg-[#331118] border-[#591622] text-[#f43f5e]"
                : "bg-[#162920] border-[#1f4a33] text-[#3ecf8e]"
            }`}
          >
            {isTripped ? (
              <ShieldAlert className="h-4 w-4" strokeWidth={1.5} />
            ) : (
              <ShieldCheck className="h-4 w-4" strokeWidth={1.5} />
            )}
          </div>
        </div>
        <div className="mt-3 flex items-baseline gap-2">
          <span
            className={`font-mono text-sm font-semibold px-2 py-0.5 rounded ${
              isTripped
                ? "bg-[#331118] text-[#f43f5e] border border-[#591622]"
                : "bg-[#162920] text-[#3ecf8e] border border-[#1f4a33]"
            }`}
          >
            {killswitchState}
          </span>
          <span className="font-mono text-xs text-[#707070]">
            Drawdown: {drawdown}
          </span>
        </div>
        <div className="mt-2 flex items-center gap-1.5 font-mono text-xs text-[#707070]">
          <span>HWM:</span>
          <span className="text-[#ededed]">
            ${parseFloat(snapshot?.high_water_mark || "100000").toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
        </div>
      </div>
    </div>
  );
}
