/**
 * The status header: engine state, strategy version, and the badge that says what this is.
 *
 * Three jobs, in order of how much trouble they cause when they fail:
 *
 * 1. **Say what this is.** The `SIMULATED / PAPER TRADING` badge is not a disclaimer in a
 *    footer. It is the loudest thing in the viewport, and it stays there.
 * 2. **Say whether the engine is running.** The state dot reads `ACTIVE`, `PAUSED`,
 *    `NEWS BLACKOUT` or `STALE FEED` — the four words an operator needs before anything else
 *    on this page is worth reading.
 * 3. **Say how old the numbers are.** `STALE_AFTER_SECONDS` is 30, which is three times the
 *    API's cache lifetime: a payload older than that is not a slow refresh, it is a stopped
 *    publisher.
 *
 * The version hash is the run's `strategy_version`, the SHA-256 of the canonical parsed
 * `config/strategy.yaml`. It is the only proof on screen that the numbers below were
 * produced by the strategy configuration the operator thinks is running.
 */

import { Banshell, Dot, Pill, cx } from "./Panel";
import { STALE_AFTER_SECONDS, type EngineSnapshot, type EngineState, type StatusPayload } from "../api/contract";
import { ago, ageSeconds, instant, shortHash } from "../lib/format";
import { Activity, Fingerprint, Radar, SignalHigh, TriangleAlert } from "lucide-react";
import type { ReactElement } from "react";
import { useNow } from "../hooks/useNow";

const STATE_LABEL: Record<EngineState, string> = {
  ACTIVE: "ACTIVE",
  PAUSED: "PAUSED",
  NEWS_BLACKOUT: "NEWS BLACKOUT",
  STALE_FEED: "STALE FEED",
  FLAT: "FLAT",
  UNKNOWN: "UNKNOWN",
};

const STATE_TONE: Record<EngineState, "positive" | "warning" | "negative" | "accent" | "neutral"> = {
  ACTIVE: "positive",
  PAUSED: "warning",
  NEWS_BLACKOUT: "warning",
  STALE_FEED: "negative",
  FLAT: "accent",
  UNKNOWN: "neutral",
};

export interface StatusHeaderProps {
  readonly status: StatusPayload | null;
  readonly snapshot: EngineSnapshot | null;
}

export function StatusHeader({ status, snapshot }: StatusHeaderProps): ReactElement {
  const now = useNow(1_000);
  const age = ageSeconds(status?.generatedAt ?? null, now);
  const stale = age !== null && age > STALE_AFTER_SECONDS;
  const state: EngineState = status?.state ?? "UNKNOWN";

  /**
   * How many of the endpoints the dashboard asked actually answered. A projection that is
   * serving three of six documents is partial, and a partial projection is a different
   * operational fact from a dead one — worth naming in the header rather than leaving the
   * operator to infer it from the sentinels below.
   */
  const answering = Object.values(snapshot?.available ?? {}).filter(Boolean).length;
  const total = Object.keys(snapshot?.available ?? {}).length;

  return (
    <div className="flex flex-col gap-4">
      <Banshell
        data-testid="status-header"
        aria-label="Engine status"
        className="overflow-hidden"
      >
        <div className="flex flex-col gap-5 px-5 py-5 sm:px-7 lg:flex-row lg:items-center lg:justify-between">
          {/* engine state ------------------------------------------------ */}
          <div className="flex items-center gap-4">
            <span
              className={cx(
                "grid h-12 w-12 shrink-0 place-items-center rounded-full ring-1",
                state === "ACTIVE"
                  ? "bg-emerald-500/10 text-emerald-300 ring-emerald-400/25"
                  : state === "UNKNOWN" || state === "STALE_FEED"
                    ? "bg-white/[0.05] text-white/50 ring-white/10"
                    : "bg-amber-500/10 text-amber-300 ring-amber-400/25",
              )}
            >
              <Activity aria-hidden strokeWidth={1.25} className="h-5 w-5" />
            </span>
            <div>
              <p className="font-display text-[0.6rem] font-medium uppercase tracking-[0.28em] text-white/40">
                Engine state
              </p>
              <p className="flex items-center gap-2.5">
                <Dot
                  tone={STATE_TONE[state]}
                  pulse={state === "ACTIVE"}
                />
                <span
                  data-testid="engine-state"
                  data-state={state}
                  className={cx(
                    "font-display text-2xl font-semibold leading-none tracking-tight",
                    STATE_TONE[state] === "positive"
                      ? "text-emerald-300"
                      : STATE_TONE[state] === "warning"
                        ? "text-amber-300"
                        : STATE_TONE[state] === "negative"
                          ? "text-rose-300"
                          : "text-white",
                  )}
                >
                  {STATE_LABEL[state]}
                </span>
                <span className="sr-only">{STATE_TONE[state]}</span>
              </p>
              <p className="mt-1.5 text-[0.72rem] leading-snug text-white/40">
                {status?.stateDetail ??
                  status?.blockedReason ??
                  "Feed state is not published by this engine build."}
              </p>
            </div>
          </div>

          {/* version + provenance ---------------------------------------- */}
          <div className="flex flex-wrap items-center gap-2.5">
            <Pill tone="accent" testId="version-hash" className="max-w-full">
              <Fingerprint aria-hidden strokeWidth={1.25} className="h-3.5 w-3.5" />
              <span className="sr-only">Strategy version</span>
              <span
                className="font-mono normal-case tracking-normal"
                title={status?.strategyVersion ?? undefined}
              >
                {status === null || status.strategyVersion === ""
                  ? "version unpublished"
                  : shortHash(status.strategyVersion)}
              </span>
            </Pill>
            <Pill tone="neutral">
              <Radar aria-hidden strokeWidth={1.25} className="h-3.5 w-3.5" />
              {status === null ? "no run" : status.runId}
            </Pill>
            <Pill tone={stale ? "negative" : "neutral"} testId="payload-age">
              <SignalHigh aria-hidden strokeWidth={1.25} className="h-3.5 w-3.5" />
              {age === null ? "no timestamp" : ago(age)}
            </Pill>
            <Pill tone="neutral">
              {status === null ? "0 bars" : `${status.bars.toLocaleString("en-US")} bars`}
            </Pill>
            {total > 0 && (
              <Pill tone={answering === total ? "neutral" : "warning"} testId="source-count">
                {answering}/{total} endpoints
              </Pill>
            )}
          </div>

          {/* the badge --------------------------------------------------- */}
          <div className="flex items-center gap-3 lg:justify-end">
            <span
              data-testid="simulated-badge"
              className="inline-flex items-center gap-2 rounded-full bg-[linear-gradient(100deg,rgba(251,191,36,0.18),rgba(244,63,94,0.16))] px-4 py-2 font-display text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-amber-200 ring-1 ring-amber-300/30"
            >
              <TriangleAlert aria-hidden strokeWidth={1.25} className="h-4 w-4" />
              Simulated / Paper Trading
            </span>
          </div>
        </div>
      </Banshell>

      {stale && (
        <div
          data-testid="stale-banner"
          role="status"
          className="flex items-center gap-3 rounded-[1.25rem] bg-rose-500/10 px-5 py-3.5 ring-1 ring-rose-400/25"
        >
          <TriangleAlert aria-hidden strokeWidth={1.25} className="h-4 w-4 shrink-0 text-rose-300" />
          <div>
            <p className="font-display text-[0.7rem] font-semibold uppercase tracking-[0.2em] text-rose-300">
              Data stale
            </p>
            <p className="text-[0.75rem] leading-snug text-rose-200/70">
              The status payload is {ago(age) ?? "older than any timestamp on record"}, beyond
              the {STALE_AFTER_SECONDS}-second threshold. Every figure below is out of date;
              the market has not necessarily stopped.
            </p>
          </div>
          <span className="ml-auto hidden shrink-0 font-display text-[0.62rem] uppercase tracking-[0.18em] text-rose-200/50 sm:block">
            {instant(status?.generatedAt ?? null, now) ?? "no timestamp"}
          </span>
        </div>
      )}
    </div>
  );
}
