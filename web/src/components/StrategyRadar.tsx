/**
 * Strategy Radar — Supabase Technical Minimalist Design.
 *
 * Designed according to DESIGN-supabase.md:
 * - Clean developer-tool look (#1c1c1c on #121212)
 * - Emerald (#3ecf8e) indicators, zero gradients, zero neons
 * - Precise 6px/8px radii and monospace data formatting
 */

import { useEffect, useState, type ReactElement } from "react";
import { Clock, Compass, Flame, Layers } from "lucide-react";
import type { AsianRange } from "../hooks/useLiveTrader";

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

interface StrategyRadarProps {
  readonly asianRange: AsianRange | null;
  readonly latestSignal: Record<string, unknown> | null;
  readonly className?: string;
}

export function StrategyRadar({
  asianRange,
  latestSignal,
  className = "",
}: StrategyRadarProps): ReactElement {
  const [nyTime, setNyTime] = useState<string>(getNyTime);

  useEffect(() => {
    const timer = setInterval(() => {
      setNyTime(getNyTime());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col gap-5 ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#ededed]">
            <Compass className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">Strategy Radar</h3>
            <p className="text-xs text-[#707070]">Session liquidity & algorithmic windows</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Real-time New York Algorithmic Reference Clock */}
          <div
            className="inline-flex items-center gap-1.5 rounded bg-[#202020] px-2.5 py-1 font-mono text-xs border border-[#2e2e2e]"
            title="Local New York (EST/EDT) algorithmic session clock"
          >
            <Clock className="h-3 w-3 text-[#3ecf8e]" />
            <span className="text-[#ededed] tabular-nums font-medium">{nyTime}</span>
            <span className="text-[10px] text-[#707070] font-sans font-semibold uppercase tracking-wider">
              New York Time
            </span>
          </div>

          <span className="inline-flex items-center gap-1.5 rounded bg-[#202020] px-2 py-1 font-mono text-xs text-[#3ecf8e] border border-[#2e2e2e]">
            <span className="h-1.5 w-1.5 rounded-full bg-[#3ecf8e]" />
            2 Active
          </span>
        </div>
      </div>

      {/* Strategy Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {/* Strategy 1: Asian Judas Swing */}
        <div className="rounded-md bg-[#202020] border border-[#282828] p-4 flex flex-col gap-2.5">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-start gap-1.5">
              <Layers className="h-3.5 w-3.5 text-[#3ecf8e] mt-0.5 shrink-0" />
              <div className="flex flex-col font-sans text-xs font-semibold text-[#ededed] leading-snug">
                <span>Asian Judas</span>
                <span>Swing</span>
              </div>
            </div>
            <div className="rounded bg-[#262626] px-2 py-1 font-mono text-[10px] border border-[#333333] text-right flex flex-col items-end leading-tight shrink-0">
              <span className="text-[#ededed] font-medium">02:00 - 05:00</span>
              <span className="text-[#707070]">New York Time</span>
            </div>
          </div>

          <div className="space-y-1 font-mono text-xs text-[#9a9a9a] pt-1 border-t border-[#282828]">
            <div className="flex justify-between">
              <span className="text-[#707070]">Asian High:</span>
              <span className="font-medium text-[#ededed]">
                {asianRange
                  ? `$${Number(asianRange.high).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                  : "Awaiting 18:00 NY"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#707070]">Asian Low:</span>
              <span className="font-medium text-[#ededed]">
                {asianRange
                  ? `$${Number(asianRange.low).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                  : "Awaiting 18:00 NY"}
              </span>
            </div>
          </div>

          <p className="text-[11px] text-[#707070] leading-relaxed">
            Sweeps Asian session extreme during London open and closes back inside the range.
          </p>
        </div>

        {/* Strategy 2: Silver Bullet */}
        <div className="rounded-md bg-[#202020] border border-[#282828] p-4 flex flex-col gap-2.5">
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-start gap-1.5">
              <Compass className="h-3.5 w-3.5 text-[#3ecf8e] mt-0.5 shrink-0" />
              <div className="flex flex-col font-sans text-xs font-semibold text-[#ededed] leading-snug">
                <span>ICT</span>
                <span>Silver Bullet</span>
              </div>
            </div>
            <div className="rounded bg-[#262626] px-2 py-1 font-mono text-[10px] border border-[#333333] text-right flex flex-col items-end leading-tight shrink-0">
              <span className="text-[#ededed] font-medium">10:00 - 11:00</span>
              <span className="text-[#707070]">New York Time</span>
            </div>
          </div>

          <div className="space-y-1 font-mono text-xs text-[#9a9a9a] pt-1 border-t border-[#282828]">
            <div className="flex justify-between">
              <span className="text-[#707070]">Window:</span>
              <span className="font-medium text-[#ededed]">New York AM Session</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#707070]">Model:</span>
              <span className="text-[#ededed]">MSS + 50% FVG Limit</span>
            </div>
          </div>

          <p className="text-[11px] text-[#707070] leading-relaxed">
            Market structure shift + Fair Value Gap midpoint entry with minimum 2R target.
          </p>
        </div>
      </div>

      {/* Signal Banner */}
      {latestSignal && (
        <div className="rounded-md bg-[#202020] border border-[#3ecf8e]/40 p-3 flex items-center justify-between font-mono text-xs">
          <div className="flex items-center gap-2">
            <Flame className="h-4 w-4 text-[#3ecf8e]" />
            <div>
              <p className="font-medium text-[#3ecf8e]">
                {String(latestSignal.strategy)} — {String(latestSignal.side)}
              </p>
              <p className="text-[11px] text-[#707070]">{String(latestSignal.reason)}</p>
            </div>
          </div>
          <div className="text-right">
            <p className="text-[#ededed] font-medium">@ ${String(latestSignal.entry)}</p>
            <p className="text-[11px] text-[#f43f5e]">SL: ${String(latestSignal.sl)}</p>
          </div>
        </div>
      )}
    </div>
  );
}
