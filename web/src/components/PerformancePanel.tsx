/**
 * The performance panels: real numbers, and the boundary above which they mean anything.
 *
 * Two panels carry performance figures in this dashboard, and both render the mandatory
 * disclosure directly adjacent to them:
 *
 * * `PerformancePanel` — the hero. Realized P&L, drawdown, expectancy in R with its
 *   bootstrap bounds, and the trade count those bounds rest on.
 * * `ExpectancyIsland` — the Level 1 headline island, which repeats the expectancy and the
 *   bounds so they are readable without scrolling.
 *
 * The `N < 30` rule is the point of both panels. `engine/metrics/performance.py` refuses to
 * compute a bootstrap interval below `MINIMUM_SAMPLE_SIZE`, because an interval over eight
 * trades is fabricated precision. This panel repeats the refusal in the operator's own
 * words — `INSUFFICIENT DATA (<30 TRADES)` — rather than printing the number anyway, and it
 * checks the sample size itself instead of trusting the payload's `status` string alone.
 */

import type { ReactElement } from "react";
import {
  MINIMUM_SAMPLE_SIZE,
  type MetricsPayload,
} from "../api/contract";
import { intervalBounds } from "../api/client";
import { currency, factor, percent, signedR } from "../lib/format";
import { Disclosure } from "./Disclosure";
import { NotPublished } from "./NotPublished";
import { Panel, Pill, Row, StatTile } from "./Panel";
import { Gauge, Scale } from "lucide-react";

/** The headline island: the number an operator reads first. */
export function ExpectancyIsland({
  metrics,
  className,
}: {
  readonly metrics: MetricsPayload | null;
  readonly className?: string;
}): ReactElement {
  if (metrics === null) {
    return (
      <Panel
        kicker="Expectancy"
        title="Expectancy in R"
        icon={<Gauge aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
        className={className}
        testId="expectancy-island"
        aside={<Pill tone="warning">no projection</Pill>}
        footer={<Disclosure context="Expectancy in R" compact testId="island-disclosure" />}
      >
        <NotPublished spec="§1" field="metrics.json" />
      </Panel>
    );
  }

  const bounds = intervalBounds(metrics);
  const expectancy = metrics.expectancyR;

  return (
    <Panel
      kicker="Expectancy"
      title="Expectancy in R"
      icon={<Gauge aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
      className={className}
      testId="expectancy-island"
      aside={
        <Pill tone={bounds === null ? "warning" : "positive"} testId="island-status">
          {bounds === null ? "insufficient" : "bounded"}
        </Pill>
      }
      footer={<Disclosure context="Expectancy in R" compact testId="island-disclosure" />}
    >
      <p
        className="font-display text-[3.25rem] font-semibold leading-none tracking-tight tabular-nums text-white"
        data-testid="island-expectancy"
      >
        {expectancy === null ? "--" : signedR(expectancy) ?? "--"}
      </p>
      <dl className="mt-5 divide-y divide-white/[0.06]">
        <Row
          label="95% bootstrap interval"
          value={
            bounds === null ? (
              <span
                data-testid="island-insufficient"
                className="font-display text-[0.72rem] uppercase tracking-[0.14em] text-amber-300"
              >
                Insufficient data (&lt;30 trades)
              </span>
            ) : (
              <span data-testid="island-bounds" className="tabular-nums">
                {signedR(bounds.lower)} → {signedR(bounds.upper)}
              </span>
            )
          }
          testId="island-interval"
        />
        <Row label="Closed trades" value={<span data-testid="island-count">{metrics.tradeCount}</span>} />
        <Row label="Win rate" value={percent(metrics.winRate) ?? "--"} />
      </dl>
    </Panel>
  );
}

/** The hero performance panel. */
export function PerformancePanel({
  metrics,
  className,
}: {
  readonly metrics: MetricsPayload | null;
  readonly className?: string;
}): ReactElement {
  if (metrics === null) {
    return (
      <Panel
        kicker="Realized performance"
        title="Realized performance"
        icon={<Scale aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
        className={className}
        testId="performance-panel"
      >
        <NotPublished spec="§1" field="metrics.json" />
        <Disclosure context="Realized performance" testId="panel-disclosure" />
      </Panel>
    );
  }

  const bounds = intervalBounds(metrics);
  const sufficient = bounds !== null;

  return (
    <Panel
      kicker="Realized performance"
      title="Realized performance"
      icon={<Scale aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
      className={className}
      testId="performance-panel"
      aside={
        <Pill tone={sufficient ? "positive" : "warning"} testId="sample-status">
          {sufficient ? `N = ${metrics.tradeCount}` : `N = ${metrics.tradeCount} · below floor`}
        </Pill>
      }
      footer={
        <div className="flex items-start justify-between gap-6">
          <span className="font-display text-[0.62rem] uppercase tracking-[0.18em] text-white/25">
            Simulated resolution · pessimistic fills
          </span>
          <span className="font-display text-[0.62rem] uppercase tracking-[0.18em] text-white/25">
            {percent(metrics.winRate) ?? "--"} win rate
          </span>
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile
          label="Realized P&L"
          value={metrics.realizedPnl.toFixed(2)}
          unit="USD"
          tone={metrics.realizedPnl > 0 ? "positive" : metrics.realizedPnl < 0 ? "negative" : "neutral"}
          baseline="Closed trades only"
          testId="stat-pnl"
        />
        <StatTile
          label="Max drawdown"
          value={metrics.maxDrawdown.toFixed(2)}
          unit="USD"
          tone={metrics.maxDrawdown > 0 ? "negative" : "neutral"}
          baseline="Peak to trough"
          testId="stat-drawdown"
        />
        <StatTile
          label="Expectancy"
          value={metrics.expectancyR === null ? "--" : metrics.expectancyR.toFixed(2)}
          unit="R"
          tone={
            metrics.expectancyR === null
              ? "neutral"
              : metrics.expectancyR > 0
                ? "positive"
                : "negative"
          }
          baseline="Mean R multiple"
          testId="stat-expectancy"
        />
        <StatTile
          label="Trade count"
          value={String(metrics.tradeCount)}
          tone={metrics.tradeCount >= MINIMUM_SAMPLE_SIZE ? "neutral" : "warning"}
          baseline={
            metrics.tradeCount >= MINIMUM_SAMPLE_SIZE
              ? "At or above the bootstrap floor"
              : `Below the ${MINIMUM_SAMPLE_SIZE}-trade floor`
          }
          testId="stat-count"
        />
      </div>

      <dl className="mt-5 divide-y divide-white/[0.06] border-t border-white/[0.06] pt-1">
        <Row
          label="Expectancy in R"
          hint="mean of the R multiples"
          value={<span data-testid="row-expectancy">{signedR(metrics.expectancyR) ?? "--"}</span>}
        />
        <Row
          label="95% bootstrap interval"
          hint={`over ${metrics.tradeCount} closed trades`}
          value={
            sufficient ? (
              <span data-testid="confidence-bounds" className="tabular-nums">
                {signedR(bounds.lower)} <span className="text-white/35">→</span> {signedR(bounds.upper)}
              </span>
            ) : (
              <span
                data-testid="insufficient-data"
                className="font-display text-[0.72rem] uppercase tracking-[0.14em] text-amber-300"
              >
                Insufficient data (&lt;30 trades)
              </span>
            )
          }
          testId="row-interval"
        />
        <Row
          label="Profit factor"
          value={factor(metrics.profitFactor) ?? "undefined — no losing trades"}
        />
        <Row
          label="Win rate"
          value={
            percent(metrics.winRate) === null ? (
              <span className="text-white/40">no closed trades</span>
            ) : (
              percent(metrics.winRate)
            )
          }
        />
        <Row
          label="Realized P&L"
          value={currency(metrics.realizedPnl.toFixed(2), { signed: true }) ?? "--"}
        />
      </dl>

      {!sufficient && (
        <p
          data-testid="insufficient-explainer"
          className="mt-4 rounded-[1.1rem] bg-amber-500/[0.07] p-4 text-[0.72rem] leading-relaxed text-amber-100/70 ring-1 ring-amber-400/20"
        >
          No interval is shown. A bootstrap over {metrics.tradeCount} trades would print a
          span whose width is an artefact of the sample, not a measure of it. The point
          estimates above are still reported, and are exactly as reliable as the count
          implies.
        </p>
      )}

      <div className="mt-6" data-testid="disclosure-anchor">
        <Disclosure context="Realized performance" testId="panel-disclosure" />
      </div>
    </Panel>
  );
}
