/**
 * VAR_117 Multi-Asset ETF Momentum Rotation Dashboard.
 *
 * Designed according to DESIGN-supabase.md:
 * - Minimalist chrome, near-monochrome palette (#1c1c1c on #121212)
 * - Single signature emerald-green (#3ecf8e) as the primary highlight
 * - Strictly NO neons, NO text gradients, solid typography
 * - Clean technical 1px hairline borders
 */

import { useEffect, useState, type ReactElement } from "react";
import { Clock } from "lucide-react";
import { useLiveTrader } from "./hooks/useLiveTrader";
import { PortfolioSummaryCards } from "./components/PortfolioSummaryCards";
import { HoldingsTable } from "./components/HoldingsTable";
import { ETFRadar } from "./components/ETFRadar";
import { RebalanceTimeline } from "./components/RebalanceTimeline";

function getNyTime(): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(new Date());
  } catch {
    return new Date().toLocaleTimeString();
  }
}

export function App(): ReactElement {
  const { connected, snapshot, triggerRebalance, isRebalancing } = useLiveTrader();
  const [nyTime, setNyTime] = useState<string>(getNyTime);

  useEffect(() => {
    const timer = setInterval(() => {
      setNyTime(getNyTime());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const brokerMode = snapshot?.broker_mode || "Simulated Paper";

  return (
    <div className="min-h-screen bg-[#121212] text-[#ededed] antialiased">
      {/* Top Navigation */}
      <header className="border-b border-[#242424] bg-[#171717] px-6 py-3 sticky top-0 z-50">
        <div className="mx-auto flex max-w-[94rem] items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-7 w-7 items-center justify-center rounded bg-[#3ecf8e] text-[#121212] font-bold text-xs tracking-tight">
              GB
            </div>
            <div className="flex items-center gap-2">
              <span className="font-sans text-sm font-semibold text-[#ededed]">
                GBOT
              </span>
              <span className="text-[#333333]">/</span>
              <span className="font-mono text-xs text-[#9a9a9a]">
                VAR_117 Multi-Asset ETF Momentum
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3 font-mono text-xs">
            {/* NY Algorithmic Clock */}
            <div className="hidden sm:flex items-center gap-2 rounded bg-[#202020] px-3 py-1 border border-[#282828] text-[#9a9a9a]">
              <Clock className="h-3.5 w-3.5 text-[#3ecf8e]" />
              <span>NY: {nyTime}</span>
            </div>

            {/* Broker Badge */}
            <div className="flex items-center rounded bg-[#202020] px-3 py-1 border border-[#282828] text-[#ededed]">
              <span className="text-[#707070] mr-1.5">Broker:</span>
              <span className="font-semibold text-[#3ecf8e]">{brokerMode}</span>
            </div>

            {/* Engine WebSocket Connection Status */}
            <div className="flex items-center gap-2 rounded bg-[#202020] px-3 py-1 border border-[#282828]">
              <span
                className={`h-2 w-2 rounded-full ${
                  connected ? "bg-[#3ecf8e]" : "bg-[#f43f5e] animate-pulse"
                }`}
              />
              <span className="text-[#9a9a9a]">
                {connected ? "Engine Live" : "Connecting..."}
              </span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content Dashboard */}
      <main className="mx-auto max-w-[94rem] p-6 flex flex-col gap-6">
        {/* Metric Cards Banner */}
        <PortfolioSummaryCards snapshot={snapshot} />

        {/* 2-Column Responsive Layout */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left Column: Active Holdings & Rebalance Tape */}
          <div className="lg:col-span-7 flex flex-col gap-6">
            <HoldingsTable holdings={snapshot?.holdings || []} />
            <RebalanceTimeline
              rebalanceInfo={snapshot?.rebalance_info || null}
              recentOrders={snapshot?.recent_orders || []}
            />
          </div>

          {/* Right Column: 12-ETF Momentum Strategy Radar */}
          <div className="lg:col-span-5 flex flex-col gap-6">
            <ETFRadar
              scores={snapshot?.radar || []}
              onRebalance={triggerRebalance}
              isRebalancing={isRebalancing}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
