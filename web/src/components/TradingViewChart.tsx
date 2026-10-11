/**
 * Candlestick Chart — Supabase Technical Minimalist Design.
 *
 * Designed according to DESIGN-supabase.md:
 * - Clean matte dark canvas (#171717 inside #1c1c1c)
 * - Supabase Emerald (#3ecf8e) bull candles and clean red (#f43f5e) bear candles
 * - No neons, no text gradients, subtle 1px grid hairlines
 * - Technical rounded-lg (12px) borders
 */

import { useEffect, useRef, type ReactElement } from "react";
import {
  createChart,
  CandlestickSeries,
  ColorType,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { BarChart3 } from "lucide-react";
import type { AsianRange, LiveBar, LiveTick } from "../hooks/useLiveTrader";

interface TradingViewChartProps {
  readonly bars: readonly LiveBar[];
  readonly tick: LiveTick | null;
  readonly asianRange: AsianRange | null;
  readonly className?: string;
}

export function TradingViewChart({
  bars,
  tick,
  asianRange,
  className = "",
}: TradingViewChartProps): ReactElement {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<any> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    const container = chartContainerRef.current;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 380,
      layout: {
        background: { type: ColorType.Solid, color: "#171717" },
        textColor: "#9a9a9a",
        fontFamily: "ui-monospace, Menlo, Monaco, Consolas, monospace",
      },
      grid: {
        vertLines: { color: "#222222" },
        horzLines: { color: "#222222" },
      },
      crosshair: {
        vertLine: { color: "#707070", width: 1 },
        horzLine: { color: "#707070", width: 1 },
      },
      rightPriceScale: {
        borderColor: "#282828",
        scaleMargins: { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderColor: "#282828",
        timeVisible: true,
        secondsVisible: false,
      },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#3ecf8e",
      downColor: "#f43f5e",
      borderUpColor: "#3ecf8e",
      borderDownColor: "#f43f5e",
      wickUpColor: "#3ecf8e",
      wickDownColor: "#f43f5e",
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
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

  // Set historical bars
  useEffect(() => {
    if (!seriesRef.current || bars.length === 0) return;

    const formattedData = bars
      .map((b) => ({
        time: b.time as UTCTimestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
      .sort((a, b) => a.time - b.time);

    const unique = formattedData.filter((item, index, self) =>
      index === self.findIndex((t) => t.time === item.time)
    );

    seriesRef.current.setData(unique);
  }, [bars]);

  // Update current bar with live ticks
  useEffect(() => {
    if (!seriesRef.current || !tick || bars.length === 0) return;

    const lastBar = bars[bars.length - 1];
    const currentPrice = parseFloat(tick.mid);

    seriesRef.current.update({
      time: lastBar.time as UTCTimestamp,
      open: lastBar.open,
      high: Math.max(lastBar.high, currentPrice),
      low: Math.min(lastBar.low, currentPrice),
      close: currentPrice,
    });
  }, [tick, bars]);

  // Plot Asian High & Low Price Lines
  useEffect(() => {
    if (!seriesRef.current || !asianRange) return;

    const highPrice = parseFloat(asianRange.high);
    const lowPrice = parseFloat(asianRange.low);

    const highLine = seriesRef.current.createPriceLine({
      price: highPrice,
      color: "#3ecf8e",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "ASIAN HIGH",
    });

    const lowLine = seriesRef.current.createPriceLine({
      price: lowPrice,
      color: "#707070",
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "ASIAN LOW",
    });

    return () => {
      if (seriesRef.current) {
        seriesRef.current.removePriceLine(highLine);
        seriesRef.current.removePriceLine(lowLine);
      }
    };
  }, [asianRange]);

  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col gap-4 ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#ededed]">
            <BarChart3 className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">M5 Candlestick Chart</h3>
            <p className="text-xs text-[#707070]">Real-time execution & ICT session levels</p>
          </div>
        </div>

        <div className="flex items-center gap-2 font-mono text-xs">
          {asianRange && (
            <span className="rounded bg-[#202020] px-2.5 py-1 text-[#9a9a9a] border border-[#2e2e2e]">
              Asian Range: <span className="text-[#3ecf8e]">{asianRange.low}</span> - <span className="text-[#3ecf8e]">{asianRange.high}</span>
            </span>
          )}
          <span className="rounded bg-[#202020] px-2 py-1 text-[#707070] border border-[#2e2e2e]">
            M5
          </span>
        </div>
      </div>

      {/* Chart Canvas */}
      <div className="relative w-full overflow-hidden rounded-md border border-[#282828] bg-[#171717]">
        <div ref={chartContainerRef} className="w-full" style={{ minHeight: "380px" }} />
      </div>
    </div>
  );
}
