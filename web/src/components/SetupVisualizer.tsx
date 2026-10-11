/**
 * The setup visualizer: closed M5 bars, with the sweep, the shift and the gap on top.
 *
 * The chart is hand-built SVG. A charting library would buy axis niceties and cost a
 * quarter of this bundle, and none of them would know what a liquidity sweep is — the
 * overlay is the point of this view, not a decoration applied to candles.
 *
 * Three overlays, each drawn only from **confirmed** geometry:
 *
 * * the **liquidity sweep** level — the horizontal line the price pierced and reclaimed,
 *   with a tick at the bar that pierced it;
 * * the **market structure shift** — the close through the broken swing point, drawn as a
 *   marker over the bar that made it public knowledge;
 * * the **fair value gap** — the three-bar imbalance, drawn as a box between its bottom and
 *   its top, with the 50% midpoint the limit entry rested at.
 *
 * That last caveat is the one that matters. `strategy/detectors.py` deliberately separates
 * ``index`` from ``confirmed_index``, because a swing is only knowable ``swing_n`` bars
 * after it prints, and Phase 4 proved the replay engine cannot see one early. The snapshot
 * this reads is specified the same way (`docs/PHASE_5X_SPEC.md` §4): publish confirmed
 * geometry only, or the dashboard becomes the one place that leaks the future. A bar that
 * has not closed is never drawn here.
 *
 * Bars and prices are integer ticks on the wire. The y-axis is scaled in ticks and only the
 * tick labels are divided through the tick size, so the chart contains no float arithmetic.
 */

import type { ReactElement } from "react";
import type { Bar, Level, SetupsPayload } from "../api/contract";
import { formatDecimal, parseDecimal, ticksToDecimal } from "../lib/decimal";
import { NotPublished } from "./NotPublished";
import { Panel, Pill } from "./Panel";
import { CandlestickChart, Layers } from "lucide-react";

/** The SVG viewBox. Large enough for a 3-hour M5 window, fixed so layout never shifts. */
const VIEW_WIDTH = 760;
const VIEW_HEIGHT = 300;
const PADDING = { top: 18, right: 64, bottom: 26, left: 12 };
const PLOT_WIDTH = VIEW_WIDTH - PADDING.left - PADDING.right;
const PLOT_HEIGHT = VIEW_HEIGHT - PADDING.top - PADDING.bottom;

export interface SetupVisualizerProps {
  readonly setups: SetupsPayload | null;
  readonly tickSize: string | null;
  readonly className?: string;
}

interface Scale {
  readonly min: number;
  readonly max: number;
  x: (index: number) => number;
  y: (ticks: number) => number;
}

/** Map bar index and tick price into the viewBox. */
function scaleFor(bars: readonly Bar[], overlays: readonly number[][]): Scale {
  const lows = bars.map((bar) => bar.l);
  const highs = bars.map((bar) => bar.h);
  const values = [...lows, ...highs, ...overlays.flat()];
  const low = values.length > 0 ? Math.min(...values) : 0;
  const high = values.length > 0 ? Math.max(...values) : 1;
  const span = Math.max(high - low, 1);
  const inset = Math.max(span * 0.08, 4);
  const min = low - inset;
  const max = high + inset;
  const range = max - min;

  const slot = PLOT_WIDTH / Math.max(bars.length, 1);
  return {
    min,
    max,
    x: (index: number) => PADDING.left + slot * (index + 0.5),
    y: (ticks: number) => PADDING.top + PLOT_HEIGHT * (1 - (ticks - min) / range),
  };
}

/** Bar width as a fraction of its slot, so the candles keep their hairline silhouette. */
function bodyWidth(bars: readonly Bar[]): number {
  return Math.max(2, (PLOT_WIDTH / Math.max(bars.length, 1)) * 0.56);
}

export function SetupVisualizer({
  setups,
  tickSize,
  className,
}: SetupVisualizerProps): ReactElement {
  if (setups === null || setups.bars.length === 0) {
    return (
      <Panel
        kicker="Setup visualizer"
        title="Liquidity sweep · MSS · FVG"
        icon={<Layers aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
        className={className}
        testId="setup-visualizer"
        aside={<Pill tone="warning">no bars</Pill>}
      >
        <NotPublished
          spec="§4"
          field="setups.json — bars, sweep, mss, fvg, levels"
          testId="setups-missing"
        />
      </Panel>
    );
  }

  const bars = setups.bars;
  const scale = scaleFor(bars, [
    setups.sweep === null ? [] : [setups.sweep.level, setups.sweep.breach],
    setups.mss === null ? [] : [setups.mss.brokenSwing, setups.mss.close],
    setups.fvg === null ? [] : [setups.fvg.top, setups.fvg.bottom, setups.fvg.midpoint],
    setups.order === null
      ? []
      : [setups.order.entry, setups.order.stop, setups.order.target],
  ]);
  const width = bodyWidth(bars);
  const tickUnit = tickSize === null ? null : parseDecimal(tickSize);
  const label = (ticks: number): string => {
    if (tickUnit === null) {
      return String(ticks);
    }
    const value = ticksToDecimal(ticks, tickUnit);
    return value === null ? String(ticks) : formatDecimal(value, { grouping: false, fraction: 2 });
  };

  const sweepBar = indexOfBar(bars, setups.sweep?.fromUtc);
  const mssBar = indexOfBar(bars, setups.mss?.fromUtc);
  const fvgFirst = indexOfBar(bars, setups.fvg?.fromUtc);
  const fvgLast = indexOfBar(bars, setups.fvg?.toUtc);

  return (
    <Panel
      kicker="Setup visualizer"
      title={`${setups.window ?? "current"} window · sweep → shift → gap`}
      icon={<CandlestickChart aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
      className={className}
      testId="setup-visualizer"
      aside={
        <div className="flex flex-wrap gap-2">
          <Pill tone={setups.sweep === null ? "neutral" : "warning"}>sweep</Pill>
          <Pill tone={setups.mss === null ? "neutral" : "accent"}>MSS</Pill>
          <Pill tone={setups.fvg === null ? "neutral" : "positive"}>FVG</Pill>
        </div>
      }
      footer={
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 font-display text-[0.6rem] uppercase tracking-[0.16em] text-white/30">
          <span className="text-amber-300/70">sweep level</span>
          <span className="text-cyan-300/70">MSS close</span>
          <span className="text-emerald-300/70">FVG 50%</span>
          <span className="text-rose-300/70">resting stop</span>
          <span className="ml-auto">
            {bars.length} closed M5 bars · confirmed geometry only
          </span>
        </div>
      }
    >
      <svg
        viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
        role="img"
        aria-label="Candlestick chart of the current silver-bullet window with the liquidity sweep level, the market structure shift marker and the fair value gap box"
        data-testid="candlestick-chart"
        className="h-auto w-full"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* price grid ------------------------------------------------------ */}
        {gridLines(scale).map((ticks) => (
          <g key={`grid-${ticks}`}>
            <line
              x1={PADDING.left}
              x2={VIEW_WIDTH - PADDING.right}
              y1={scale.y(ticks)}
              y2={scale.y(ticks)}
              stroke="rgba(255,255,255,0.05)"
              strokeWidth={1}
            />
            <text
              x={VIEW_WIDTH - PADDING.right + 8}
              y={scale.y(ticks) + 3.5}
              fill="rgba(255,255,255,0.25)"
              fontSize={9}
              fontFamily="Geist Variable, monospace"
            >
              {label(ticks)}
            </text>
          </g>
        ))}

        {/* session and fractal levels ------------------------------------- */}
        {setups.levels.map((level: Level, index: number) => {
          if (level.ticks === null) {
            return null;
          }
          return (
            <line
              key={`level-${index}-${level.kind}`}
              data-testid={`level-${level.kind}`}
              x1={PADDING.left}
              x2={VIEW_WIDTH - PADDING.right}
              y1={scale.y(level.ticks)}
              y2={scale.y(level.ticks)}
              stroke="rgba(255,255,255,0.14)"
              strokeWidth={1}
              strokeDasharray="2 5"
            />
          );
        })}

        {/* candles -------------------------------------------------------- */}
        {bars.map((bar, index) => {
          const up = bar.c >= bar.o;
          const colour = up ? "rgba(52,211,153,0.9)" : "rgba(244,63,94,0.9)";
          const bodyTop = scale.y(Math.max(bar.o, bar.c));
          const bodyBottom = scale.y(Math.min(bar.o, bar.c));
          return (
            <g key={`bar-${bar.t}`} data-testid="candle">
              <line
                x1={scale.x(index)}
                x2={scale.x(index)}
                y1={scale.y(bar.h)}
                y2={scale.y(bar.l)}
                stroke={colour}
                strokeWidth={1}
              />
              <rect
                x={scale.x(index) - width / 2}
                y={bodyTop}
                width={width}
                height={Math.max(1, bodyBottom - bodyTop)}
                fill={up ? "rgba(52,211,153,0.28)" : "rgba(244,63,94,0.28)"}
                stroke={colour}
                strokeWidth={1}
                rx={1}
              />
            </g>
          );
        })}

        {/* fair value gap -------------------------------------------------- */}
        {setups.fvg !== null && fvgFirst !== null && fvgLast !== null && (
          <g data-testid="fvg-box">
            <rect
              x={scale.x(fvgFirst) - width / 2}
              y={scale.y(setups.fvg.top)}
              width={Math.max(
                width,
                scale.x(fvgLast) - scale.x(fvgFirst) + width,
              )}
              height={Math.max(1, scale.y(setups.fvg.bottom) - scale.y(setups.fvg.top))}
              fill="rgba(16,185,129,0.13)"
              stroke="rgba(16,185,129,0.55)"
              strokeWidth={1}
              rx={3}
            />
            <line
              x1={PADDING.left}
              x2={VIEW_WIDTH - PADDING.right}
              y1={scale.y(setups.fvg.midpoint)}
              y2={scale.y(setups.fvg.midpoint)}
              stroke="rgba(16,185,129,0.6)"
              strokeWidth={1}
              strokeDasharray="4 4"
            />
            <text
              x={PADDING.left + 6}
              y={scale.y(setups.fvg.midpoint) - 5}
              fill="rgba(110,231,183,0.85)"
              fontSize={9}
              fontFamily="Geist Variable, monospace"
            >
              {`FVG 50% ${label(setups.fvg.midpoint)}`}
            </text>
          </g>
        )}

        {/* liquidity sweep ------------------------------------------------- */}
        {setups.sweep !== null && (
          <g data-testid="sweep-level">
            <line
              x1={PADDING.left}
              x2={VIEW_WIDTH - PADDING.right}
              y1={scale.y(setups.sweep.level)}
              y2={scale.y(setups.sweep.level)}
              stroke="rgba(251,191,36,0.8)"
              strokeWidth={1.25}
              strokeDasharray="6 4"
            />
            {sweepBar !== null && (
              <>
                <line
                  x1={scale.x(sweepBar)}
                  x2={scale.x(sweepBar)}
                  y1={scale.y(setups.sweep.level)}
                  y2={scale.y(setups.sweep.breach)}
                  stroke="rgba(251,191,36,0.9)"
                  strokeWidth={1.75}
                />
                <circle
                  cx={scale.x(sweepBar)}
                  cy={scale.y(setups.sweep.breach)}
                  r={3}
                  fill="rgba(251,191,36,0.95)"
                />
              </>
            )}
            <text
              x={PADDING.left + 6}
              y={scale.y(setups.sweep.level) - 5}
              fill="rgba(253,224,71,0.9)"
              fontSize={9}
              fontFamily="Geist Variable, monospace"
            >
              {`SWEEP ${label(setups.sweep.level)} · breach ${label(setups.sweep.breach)}`}
            </text>
          </g>
        )}

        {/* market structure shift ----------------------------------------- */}
        {setups.mss !== null && mssBar !== null && (
          <g data-testid="mss-marker">
            <line
              x1={scale.x(mssBar)}
              x2={scale.x(mssBar)}
              y1={PADDING.top}
              y2={PADDING.top + PLOT_HEIGHT}
              stroke="rgba(34,211,238,0.55)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <rect
              x={scale.x(mssBar) - 4.5}
              y={scale.y(setups.mss.close) - 4.5}
              width={9}
              height={9}
              fill="rgba(34,211,238,0.9)"
              transform={`rotate(45 ${scale.x(mssBar)} ${scale.y(setups.mss.close)})`}
            />
            <text
              x={scale.x(mssBar) + 8}
              y={PADDING.top + 10}
              fill="rgba(103,232,249,0.9)"
              fontSize={9}
              fontFamily="Geist Variable, monospace"
            >
              {`MSS ${label(setups.mss.close)}`}
            </text>
          </g>
        )}

        {/* the resting order, when there is one ---------------------------- */}
        {setups.order !== null && (
          <g data-testid="resting-order">
            <line
              x1={PADDING.left}
              x2={VIEW_WIDTH - PADDING.right}
              y1={scale.y(setups.order.entry)}
              y2={scale.y(setups.order.entry)}
              stroke="rgba(255,255,255,0.22)"
              strokeWidth={1}
            />
            <line
              x1={PADDING.left}
              x2={VIEW_WIDTH - PADDING.right}
              y1={scale.y(setups.order.stop)}
              y2={scale.y(setups.order.stop)}
              stroke="rgba(244,63,94,0.55)"
              strokeWidth={1}
              strokeDasharray="2 4"
            />
            <line
              x1={PADDING.left}
              x2={VIEW_WIDTH - PADDING.right}
              y1={scale.y(setups.order.target)}
              y2={scale.y(setups.order.target)}
              stroke="rgba(255,255,255,0.2)"
              strokeWidth={1}
              strokeDasharray="2 4"
            />
          </g>
        )}

        {/* time axis ------------------------------------------------------- */}
        {bars.map((bar, index) =>
          index % 4 === 0 ? (
            <text
              key={`axis-${bar.t}`}
              x={scale.x(index)}
              y={VIEW_HEIGHT - 8}
              fill="rgba(255,255,255,0.22)"
              fontSize={8.5}
              textAnchor="middle"
              fontFamily="Geist Variable, monospace"
            >
              {bar.t.slice(11, 16)}Z
            </text>
          ) : null,
        )}
      </svg>

      {tickSize === null && (
        <p
          data-testid="tick-size-missing"
          className="mt-3 rounded-[1.1rem] bg-white/[0.02] p-3 text-[0.68rem] leading-relaxed text-white/45 ring-1 ring-white/[0.06]"
        >
          The instrument geometry is not published, so tick counts are drawn as raw integers
          on the axis rather than prices. Specified in docs/PHASE_5X_SPEC.md §3.
        </p>
      )}
    </Panel>
  );
}

/** Four price lines, spread across the plotted range. */
function gridLines(scale: Scale): number[] {
  const lines: number[] = [];
  for (let step = 0; step <= 4; step += 1) {
    lines.push(Math.round(scale.min + ((scale.max - scale.min) * step) / 4));
  }
  return lines;
}

/** The bar index a marker's window opens on, by timestamp. */
function indexOfBar(bars: readonly Bar[], iso: string | null | undefined): number | null {
  if (iso === null || iso === undefined) {
    return null;
  }
  const stamp = Date.parse(iso);
  if (Number.isNaN(stamp)) {
    return null;
  }
  const index = bars.findIndex((bar) => Date.parse(bar.t) === stamp);
  return index === -1 ? null : index;
}
