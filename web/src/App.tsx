/**
 * VAR_117 Multi-Asset ETF Momentum Rotation Dashboard.
 *
 * Designed according to DESIGN-supabase.md:
 * - Minimalist institutional chrome, dark palette (#1c1c1c on #121212)
 * - Single signature emerald-green (#3ecf8e) as the primary highlight
 * - Interactive TradingView / Lightweight-Charts portfolio equity performance curve
 * - SVG Asset Allocation visualizer
 * - Resilient offline snapshot mode: when the local PC engine is turned off,
 *   the dashboard automatically preserves and showcases the latest verified state.
 */

import { useEffect, useState, type ReactElement } from "react";
import { Clock, ShieldCheck, Radio } from "lucide-react";
import { useLiveTrader } from "./hooks/useLiveTrader";
import { PortfolioSummaryCards } from "./components/PortfolioSummaryCards";
import { PortfolioPerformanceChart } from "./components/PortfolioPerformanceChart";
import { AllocationVisualizer } from "./components/AllocationVisualizer";
import { HoldingsTable } from "./components/HoldingsTable";
import { ETFRadar } from "./components/ETFRadar";
import { RebalanceTimeline } from "./components/RebalanceTimeline";
import logoImg from "./assets/logo.png";

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

  const brokerMode = snapshot?.broker_mode || "PAPER";
  const cashBalance = snapshot?.cash_balance || "467.51";
  const accountEquity = snapshot?.account_equity || "99317.94";

  return (
    <div className="min-h-screen bg-[#121212] text-[#ededed] antialiased selection:bg-[#3ecf8e]/20">
      {/* Top Navigation */}
      <header className="border-b border-[#242424] bg-[#171717] px-6 py-3 sticky top-0 z-50">
        <div className="mx-auto flex max-w-[94rem] items-center justify-between">
          <div className="flex items-center gap-3">
            <img
              src={logoImg}
              alt="SanAlgo Logo"
              className="h-7 w-7 rounded-md object-contain shadow-sm"
            />
            <div className="flex items-center gap-2">
              <span className="font-sans text-sm font-semibold tracking-tight text-[#ededed]">
                SanAlgo
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

            {/* Engine Live Stream vs Offline Snapshot Badge */}
            <div
              className={`flex items-center gap-2 rounded px-3 py-1 border transition-colors ${
                connected
                  ? "bg-[#183427] border-[#24523b] text-[#3ecf8e]"
                  : "bg-[#202020] border-[#282828] text-[#9a9a9a]"
              }`}
            >
              {connected ? (
                <>
                  <Radio className="h-3.5 w-3.5 text-[#3ecf8e] animate-pulse" />
                  <span>Engine Live</span>
                </>
              ) : (
                <>
                  <ShieldCheck className="h-3.5 w-3.5 text-[#38bdf8]" />
                  <span>Offline Snapshot</span>
                </>
              )}
            </div>
          </div>
        </div>
      </header>

      {/* Offline Showcase Status Notice Banner */}
      {!connected && (
        <div className="border-b border-[#242424] bg-[#171717]/80 backdrop-blur px-6 py-2 text-xs font-mono">
          <div className="mx-auto flex max-w-[94rem] items-center justify-between text-[#9a9a9a]">
            <div className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-[#38bdf8]" />
              <span>
                <strong className="text-[#ededed]">Showcase Mode Active</strong> — Displaying verified portfolio state from Alpaca ($99,317.94 NAV • 3 Active ETFs).
              </span>
            </div>
            <span className="hidden md:inline text-[11px] text-[#707070]">
              Start local engine on PC to resume sub-second WebSocket ticks.
            </span>
          </div>
        </div>
      )}

      {/* Main Content Dashboard */}
      <main className="mx-auto max-w-[94rem] p-6 flex flex-col gap-6">
        {/* Metric Cards Banner */}
        <PortfolioSummaryCards snapshot={snapshot} />

        {/* HERO SECTION: Interactive Performance Graph & Asset Allocation Donut */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
          {/* Left: Interactive Portfolio Performance Area Chart */}
          <div className="lg:col-span-8 flex flex-col">
            <PortfolioPerformanceChart
              snapshot={snapshot}
              isConnected={connected}
              className="h-full"
            />
          </div>

          {/* Right: Asset Allocation Donut & Target Weights */}
          <div className="lg:col-span-4 flex flex-col">
            <AllocationVisualizer
              holdings={snapshot?.holdings || []}
              cashBalance={cashBalance}
              totalEquity={accountEquity}
              className="h-full"
            />
          </div>
        </div>

        {/* SECONDARY SECTION: Current Portfolio Holdings Table, Order History & 12-ETF Momentum Radar */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
          {/* Left Column: Active Holdings Table & Execution History / Orders */}
          <div className="lg:col-span-7 flex flex-col gap-6">
            <HoldingsTable holdings={snapshot?.holdings || []} />
            <RebalanceTimeline
              rebalanceInfo={snapshot?.rebalance_info || null}
              recentOrders={snapshot?.recent_orders || []}
              className="flex-1"
            />
          </div>

          {/* Right Column: 12-ETF Momentum Strategy Radar */}
          <div className="lg:col-span-5 flex flex-col">
            <ETFRadar
              scores={snapshot?.radar || []}
              onRebalance={triggerRebalance}
              isRebalancing={isRebalancing}
              className="h-full"
            />
          </div>
        </div>
      </main>
    </div>
  );
}
