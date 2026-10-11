/**
 * XAUUSD Institutional Trading Dashboard — Supabase Minimalist Design System.
 *
 * Designed according to DESIGN-supabase.md:
 * - Minimalist chrome, near-monochrome palette (#1c1c1c on #121212)
 * - Single signature emerald-green (#3ecf8e) as the primary highlight
 * - Strictly NO neons, NO text gradients, solid typography
 * - Clean technical 1px hairline borders
 */

import type { ReactElement } from "react";
import { useLiveTrader } from "./hooks/useLiveTrader";
import { LiveTickerCard } from "./components/LiveTickerCard";
import { TradingViewChart } from "./components/TradingViewChart";
import { LivePositionPnL } from "./components/LivePositionPnL";
import { StrategyRadar } from "./components/StrategyRadar";
import { LiveTradeTape } from "./components/LiveTradeTape";

export interface AppProps {
  readonly snapshot?: any;
}

export function App(_props: AppProps = {}): ReactElement {
  const { connected, snapshot, latestTick, priceFlash } = useLiveTrader();

  return (
    <div className="min-h-screen bg-[#121212] text-[#ededed] antialiased">
      {/* Top Navigation */}
      <header className="border-b border-[#242424] bg-[#171717] px-6 py-3">
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
                XAUUSD ICT Lab
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3 font-mono text-xs">
            <div className="flex items-center gap-2 rounded bg-[#202020] px-3 py-1 border border-[#282828]">
              <span
                className={`h-2 w-2 rounded-full ${
                  connected ? "bg-[#3ecf8e]" : "bg-[#f43f5e] animate-pulse"
                }`}
              />
              <span className="text-[#9a9a9a]">
                {connected ? "MetaTrader 5 Connected" : "Connecting to Engine..."}
              </span>
            </div>

            <div className="hidden sm:flex items-center rounded bg-[#202020] px-3 py-1 border border-[#282828] text-[#707070]">
              <span>Demo #414431426</span>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <div className="mx-auto flex max-w-[94rem] flex-col gap-6 px-6 py-6">
        {/* Hero Section: Live Ticker Card */}
        <section>
          <LiveTickerCard
            snapshot={snapshot}
            tick={latestTick}
            connected={connected}
            priceFlash={priceFlash}
          />
        </section>

        {/* Technical Grid Layout */}
        <main className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          {/* Real-Time Interactive Candlestick Chart (7 cols) */}
          <section className="lg:col-span-7">
            <TradingViewChart
              bars={snapshot?.recent_bars ?? []}
              tick={latestTick}
              asianRange={snapshot?.asian_range ?? null}
              className="h-full"
            />
          </section>

          {/* Live Position & Today's PnL Island (5 cols) */}
          <section className="lg:col-span-5">
            <LivePositionPnL snapshot={snapshot} className="h-full" />
          </section>

          {/* Strategy Radar (5 cols) */}
          <section className="lg:col-span-5">
            <StrategyRadar
              asianRange={snapshot?.asian_range ?? null}
              latestSignal={snapshot?.latest_signal ?? null}
              className="h-full"
            />
          </section>

          {/* Trade History (7 cols) */}
          <section className="lg:col-span-7">
            <LiveTradeTape
              trades={snapshot?.completed_trades ?? []}
              className="h-full"
            />
          </section>
        </main>

        {/* Minimalist Technical Footer */}
        <footer className="mt-8 border-t border-[#242424] pt-4 pb-6 text-center font-mono text-[11px] text-[#707070]">
          GBOT Quantitative Research · ICT Asian Judas Swing & Silver Bullet · MetaTrader 5 Bridge
        </footer>
      </div>
    </div>
  );
}
