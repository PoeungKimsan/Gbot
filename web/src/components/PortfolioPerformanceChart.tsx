/**
 * Portfolio Performance Chart — Institutional Interactive Equity Curve.
 *
 * Modeled after institutional terminal & Alpaca portfolio performance views:
 * - Real-time NAV display with period return badge
 * - Timeframe controls: 1D, 2D, 1W, 1M, 1Y, ALL (defaults to 2D)
 * - Accurate historical data from broker (/api/portfolio/history) with live WebSocket ticks
 * - Interactive TradingView / Lightweight-Charts AreaSeries with smooth dynamic gradient fill
 * - Dynamic color-coding: Emerald green when period return >= 0, Rose red when < 0
 * - Crosshair inspection tooltip showing exact point-in-time equity value and timestamp
 */

import { useEffect, useRef, useState, type ReactElement } from "react";
import {
  createChart,
  AreaSeries,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { LiveSnapshot } from "../hooks/useLiveTrader";

interface PortfolioPerformanceChartProps {
  readonly snapshot: LiveSnapshot | null;
  readonly isConnected: boolean;
  readonly className?: string;
}

export type Timeframe = "1D" | "2D" | "1W" | "1M" | "3M" | "1Y" | "ALL";

interface ChartPoint {
  time: UTCTimestamp;
  value: number;
}

function getApiUrl(timeframe: Timeframe): string {
  const isHttps = typeof window !== "undefined" && window.location.protocol === "https:";
  const proto = isHttps ? "https:" : "http:";
  const host = typeof window !== "undefined" ? window.location.hostname || "127.0.0.1" : "127.0.0.1";
  const port =
    typeof window !== "undefined" && window.location.port && window.location.port !== "8080"
      ? "8080"
      : typeof window !== "undefined"
      ? window.location.port
      : "8080";
  return `${proto}//${host}${port ? `:${port}` : ""}/api/portfolio/history?period=${timeframe}`;
}

export function PortfolioPerformanceChart({
  snapshot,
  isConnected,
  className = "",
}: PortfolioPerformanceChartProps): ReactElement {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<any> | null>(null);

  const [timeframe, setTimeframe] = useState<Timeframe>("2D");
  const currentTimeframeRef = useRef<Timeframe>("2D");
  currentTimeframeRef.current = timeframe;

  const [historyPoints, setHistoryPoints] = useState<ChartPoint[]>([]);
  const [hoveredData, setHoveredData] = useState<{ value: number; timeStr: string } | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const currentEquityNum = snapshot?.account_equity
    ? parseFloat(snapshot.account_equity)
    : 96492.09;
  const currentCashNum = snapshot?.cash_balance ? parseFloat(snapshot.cash_balance) : 467.51;

  // Initialize and mount Lightweight-Charts instance
  useEffect(() => {
    if (!chartContainerRef.current) return;
    const container = chartContainerRef.current;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 310,
      layout: {
        background: { type: ColorType.Solid, color: "#1c1c1c" },
        textColor: "#707070",
        fontFamily: "ui-monospace, Menlo, Monaco, Consolas, monospace",
      },
      grid: {
        vertLines: { color: "#222222" },
        horzLines: { color: "#222222" },
      },
      crosshair: {
        vertLine: { color: "#444444", width: 1, style: 2 },
        horzLine: { color: "#444444", width: 1, style: 2 },
      },
      rightPriceScale: {
        borderColor: "#282828",
        scaleMargins: { top: 0.15, bottom: 0.1 },
      },
      timeScale: {
        borderColor: "#282828",
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
      },
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
      },
    });

    const series = chart.addSeries(AreaSeries, {
      topColor: "rgba(62, 207, 142, 0.32)",
      bottomColor: "rgba(62, 207, 142, 0.00)",
      lineColor: "#3ecf8e",
      lineWidth: 2,
      priceFormat: {
        type: "price",
        precision: 2,
        minMove: 0.01,
      },
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Crosshair move handler
    chart.subscribeCrosshairMove((param) => {
      if (!param || !param.time || !param.seriesData.get(series)) {
        setHoveredData(null);
        return;
      }
      const dataPoint = param.seriesData.get(series) as { value: number };
      const date = new Date((param.time as number) * 1000);
      const activeTf = currentTimeframeRef.current;
      const isIntraday = activeTf === "1D" || activeTf === "2D" || activeTf === "1W";
      const timeStr = date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        hour: isIntraday ? "2-digit" : undefined,
        minute: isIntraday ? "2-digit" : undefined,
      });
      setHoveredData({
        value: dataPoint.value,
        timeStr,
      });
    });

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, []);

  const lastFittedKeyRef = useRef<string>("");
  const historyCacheRef = useRef<Map<Timeframe, ChartPoint[]>>(new Map());

  // Background prefetch remaining popular timeframes so switching is 0ms instant
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      const toPrefetch: Timeframe[] = ["1D", "1W", "1M", "3M", "ALL"];
      for (const tf of toPrefetch) {
        if (historyCacheRef.current.has(tf)) continue;
        fetch(`/api/portfolio/history?period=${tf}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((body) => {
            if (cancelled) return;
            const rawData = Array.isArray(body?.data) ? body.data : [];
            if (rawData.length > 0) {
              const mapped: ChartPoint[] = rawData
                .filter((p: any) => p && typeof p.equity === "number" && !isNaN(p.equity))
                .map(
                  (p: any): ChartPoint => ({
                    time: (typeof p.time === "number"
                      ? p.time
                      : Math.floor(new Date(p.time).getTime() / 1000)) as UTCTimestamp,
                    value: Number(p.equity.toFixed(2)),
                  })
                )
                .sort((a: ChartPoint, b: ChartPoint) => (a.time as number) - (b.time as number))
                .filter((p: ChartPoint, i: number, arr: ChartPoint[]) => i === 0 || (p.time as number) > (arr[i - 1].time as number));
              if (mapped.length > 0) {
                historyCacheRef.current.set(tf, mapped);
              }
            }
          })
          .catch(() => {});
      }
    }, 600);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  // Fetch actual historical data from /api/portfolio/history whenever timeframe changes
  useEffect(() => {
    let cancelled = false;

    // Instant cache-hit display (<1ms)
    const cached = historyCacheRef.current.get(timeframe);
    if (cached && cached.length > 0) {
      setHistoryPoints(cached);
      setIsLoading(false);
    } else {
      setIsLoading(true);
    }

    async function loadHistory() {
      try {
        let res = await fetch(`/api/portfolio/history?period=${timeframe}`).catch(() => null);
        if (!res || !res.ok) {
          res = await fetch(getApiUrl(timeframe)).catch(() => null);
        }
        if (res && res.ok) {
          const body = await res.json();
          const rawData = Array.isArray(body?.data)
            ? body.data
            : Array.isArray(body)
            ? body
            : [];
          if (rawData.length > 0 && !cancelled) {
            const mapped: ChartPoint[] = rawData
              .filter((p: any) => p && typeof p.equity === "number" && !isNaN(p.equity))
              .map(
                (p: any): ChartPoint => ({
                  time: (typeof p.time === "number"
                    ? p.time
                    : Math.floor(new Date(p.time).getTime() / 1000)) as UTCTimestamp,
                  value: Number(p.equity.toFixed(2)),
                })
              )
              .sort((a: ChartPoint, b: ChartPoint) => (a.time as number) - (b.time as number))
              .filter((p: ChartPoint, i: number, arr: ChartPoint[]) => i === 0 || (p.time as number) > (arr[i - 1].time as number));

            if (mapped.length > 0) {
              historyCacheRef.current.set(timeframe, mapped);
              setHistoryPoints(mapped);
              setIsLoading(false);
              return;
            }
          }
        }
      } catch (err) {
        console.warn("Could not fetch portfolio history from API:", err);
      }

      if (!cancelled && !historyCacheRef.current.has(timeframe)) {
        // Fallback to snapshot.equity_history
        const rawSnapHist = snapshot?.equity_history || [];
        if (rawSnapHist.length > 0) {
          const nowSec = Math.floor(Date.now() / 1000);
          const cutoffs: Record<Timeframe, number> = {
            "1D": nowSec - 86400,
            "2D": nowSec - 172800,
            "1W": nowSec - 7 * 86400,
            "1M": nowSec - 30 * 86400,
            "3M": nowSec - 90 * 86400,
            "1Y": nowSec - 365 * 86400,
            ALL: 0,
          };
          const cutoff = cutoffs[timeframe];
          const filtered: ChartPoint[] = rawSnapHist
            .filter((p) => p.time >= cutoff && typeof p.equity === "number" && !isNaN(p.equity))
            .map((p): ChartPoint => ({
              time: p.time as UTCTimestamp,
              value: Number(p.equity.toFixed(2)),
            }))
            .sort((a: ChartPoint, b: ChartPoint) => (a.time as number) - (b.time as number))
            .filter((p: ChartPoint, i: number, arr: ChartPoint[]) => i === 0 || (p.time as number) > (arr[i - 1].time as number));

          if (filtered.length > 0) {
            setHistoryPoints(filtered);
            setIsLoading(false);
            return;
          }
        }

        // If no history at all yet, anchor to current equity
        const nowSec = Math.floor(Date.now() / 1000);
        setHistoryPoints([
          { time: (nowSec - 7200) as UTCTimestamp, value: currentEquityNum },
          { time: nowSec as UTCTimestamp, value: currentEquityNum },
        ]);
        setIsLoading(false);
      }
    }

    void loadHistory();
    return () => {
      cancelled = true;
    };
  }, [timeframe]);

  // Feed points and live updates into chart series
  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return;
    if (historyPoints.length === 0) return;

    // Clone points and ensure final point reflects current live equity
    const data = [...historyPoints];
    const nowSec = Math.floor(Date.now() / 1000) as UTCTimestamp;
    const lastPoint = data[data.length - 1];

    if (currentEquityNum && lastPoint) {
      if (nowSec > (lastPoint.time as number)) {
        data.push({ time: nowSec, value: currentEquityNum });
      } else {
        data[data.length - 1] = { time: lastPoint.time, value: currentEquityNum };
      }
    }

    const startVal = data[0]?.value || currentEquityNum;
    const isGain = currentEquityNum >= startVal;

    // Dynamic color coding based on actual performance over period
    seriesRef.current.applyOptions({
      lineColor: isGain ? "#3ecf8e" : "#f43f5e",
      topColor: isGain ? "rgba(62, 207, 142, 0.32)" : "rgba(244, 63, 94, 0.32)",
      bottomColor: isGain ? "rgba(62, 207, 142, 0.00)" : "rgba(244, 63, 94, 0.00)",
    });

    seriesRef.current.setData(data);

    chartRef.current.applyOptions({
      timeScale: {
        timeVisible: timeframe === "1D" || timeframe === "2D" || timeframe === "1W",
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
    });

    const fitKey = `${timeframe}:${data.length}`;
    if (lastFittedKeyRef.current !== fitKey) {
      lastFittedKeyRef.current = fitKey;
      requestAnimationFrame(() => {
        if (chartRef.current) {
          chartRef.current.timeScale().resetTimeScale();
          chartRef.current.timeScale().fitContent();
        }
      });
    }
  }, [historyPoints, currentEquityNum, timeframe]);

  // Calculate actual period change
  const startVal = historyPoints[0]?.value || currentEquityNum;
  const displayValue = hoveredData ? hoveredData.value : currentEquityNum;
  const deltaDollars = displayValue - startVal;
  const deltaPercent =
    startVal > 0 ? ((deltaDollars / startVal) * 100).toFixed(2) : "0.00";
  const isPositive = deltaDollars >= 0;

  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col gap-4 relative ${className}`}
    >
      {/* Header bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-[#282828] pb-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-sans text-xs font-semibold uppercase tracking-wider text-[#9a9a9a]">
              Portfolio Performance
            </span>
            <span
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono ${
                isConnected
                  ? "bg-[#183427] text-[#3ecf8e] border border-[#24523b]"
                  : "bg-[#242424] text-[#9a9a9a] border border-[#333333]"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  isConnected ? "bg-[#3ecf8e] animate-pulse" : "bg-[#707070]"
                }`}
              />
              {isConnected ? "LIVE" : "SNAPSHOT"}
            </span>
            {isLoading && (
              <span className="font-mono text-[10px] text-[#707070]">Loading...</span>
            )}
          </div>

          {/* Large NAV display with period P&L badge */}
          <div className="mt-2 flex items-baseline gap-3">
            <span className="font-mono text-3xl font-bold tracking-tight text-[#ededed]">
              ${displayValue.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
            <span
              className={`font-mono text-sm font-semibold flex items-center gap-1 ${
                isPositive ? "text-[#3ecf8e]" : "text-[#f43f5e]"
              }`}
            >
              {isPositive ? `+$${Math.abs(deltaDollars).toFixed(2)} (+${deltaPercent}%)` : `-$${Math.abs(deltaDollars).toFixed(2)} (${deltaPercent}%)`}
              <span className="text-[11px] font-normal text-[#707070] ml-0.5">{timeframe}</span>
            </span>
            {hoveredData && (
              <span className="font-mono text-xs text-[#707070]">
                at {hoveredData.timeStr}
              </span>
            )}
          </div>
        </div>

        {/* Timeframe pill tabs */}
        <div className="flex items-center gap-1 bg-[#171717] p-1 rounded-md border border-[#282828]">
          {(["1D", "2D", "1W", "1M", "3M", "1Y", "ALL"] as Timeframe[]).map((tf) => (
            <button
              key={tf}
              type="button"
              onClick={() => setTimeframe(tf)}
              className={`px-3 py-1 rounded text-xs font-mono font-medium transition-all ${
                timeframe === tf
                  ? "bg-[#282828] text-[#3ecf8e] shadow-sm font-semibold"
                  : "text-[#707070] hover:text-[#ededed] hover:bg-[#202020]"
              }`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>

      {/* Chart Canvas */}
      <div className="relative w-full overflow-hidden rounded-md bg-[#171717] border border-[#242424]">
        <div ref={chartContainerRef} className="w-full" />
      </div>

      {/* Quick stats footer under graph */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-1 text-xs font-mono">
        <div className="rounded bg-[#171717] border border-[#242424] p-2.5">
          <div className="text-[11px] text-[#707070]">Invested Capital</div>
          <div className="text-sm font-medium text-[#ededed] mt-0.5">
            ${parseFloat(snapshot?.invested_value || "0.00").toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
        </div>
        <div className="rounded bg-[#171717] border border-[#242424] p-2.5">
          <div className="text-[11px] text-[#707070]">Cash Reserve</div>
          <div className="text-sm font-medium text-[#ededed] mt-0.5">
            ${currentCashNum.toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
        </div>
        <div className="rounded bg-[#171717] border border-[#242424] p-2.5">
          <div className="text-[11px] text-[#707070]">{timeframe} Period Return</div>
          <div className={`text-sm font-medium mt-0.5 ${isPositive ? "text-[#3ecf8e]" : "text-[#f43f5e]"}`}>
            {isPositive ? `+$${Math.abs(deltaDollars).toFixed(2)}` : `-$${Math.abs(deltaDollars).toFixed(2)}`} ({deltaPercent}%)
          </div>
        </div>
        <div className="rounded bg-[#171717] border border-[#242424] p-2.5">
          <div className="text-[11px] text-[#707070]">High-Water Mark</div>
          <div className="text-sm font-medium text-[#ededed] mt-0.5">
            ${parseFloat(snapshot?.high_water_mark || "100000.00").toLocaleString("en-US", { minimumFractionDigits: 2 })}
          </div>
        </div>
      </div>
    </div>
  );
}
