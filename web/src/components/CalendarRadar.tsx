/**
 * The economic calendar radar: what is coming, and what is gating the engine right now.
 *
 * Two lists, because they answer two different questions:
 *
 * * **Countdown** — the Tier-1 releases still ahead, each with a live countdown and the
 *   blackout window that will open around it.
 * * **Gates** — the blackout windows active *right now*. This is the one that matters in an
 *   incident: a flat book during a Nonfarm Payrolls print is correct behaviour, not a
 *   stalled strategy.
 *
 * "Tier-1" is not a judgement this component makes. `engine.news.calendar` defines it as
 * ``impact: high`` *and* ``EventKind.RELEASE`` — a FRED-confirmed release, not an advisory
 * hit. `EconomicEvent.is_blocking` encodes that policy, and the payload carries it, so the
 * dashboard filtering the tier itself would be a second implementation of a rule that
 * already exists and can drift.
 *
 * The countdown is driven by the browser clock, which is honest but not authoritative.
 * `generated_at` lets an operator see the skew, and the client-side skew is shown alongside
 * it rather than hidden.
 */

import type { ReactElement } from "react";
import {
  STALE_AFTER_SECONDS,
  type CalendarEvent,
  type CalendarPayload,
} from "../api/contract";
import { ageSeconds, duration, instant } from "../lib/format";
import { NotPublished } from "./NotPublished";
import { Panel, Pill, cx } from "./Panel";
import { CalendarClock, Clock3, Radio } from "lucide-react";
import { useNow } from "../hooks/useNow";

export interface CalendarRadarProps {
  readonly calendar: CalendarPayload | null;
  /** Injected in tests; defaults to the component's own one-second clock. */
  readonly now?: Date;
  readonly className?: string;
}

/** The kind of release that may gate the engine: FRED-confirmed, high impact. */
function isTierOne(event: CalendarEvent): boolean {
  return event.kind === "RELEASE" && event.impact === "high";
}

interface Countdown {
  readonly event: CalendarEvent;
  readonly secondsUntil: number;
  readonly inBlackout: boolean;
  readonly blackoutEndsIn: number | null;
}

export function CalendarRadar({
  calendar,
  now,
  className,
}: CalendarRadarProps): ReactElement {
  /**
   * The countdown has to move on its own, so this component owns a clock rather than
   * assuming its parent threads one down. It is the only timer in the app besides the
   * header's staleness check, and both are cleared on unmount.
   */
  const clock = useNow(1_000);
  const reference = now ?? clock;
  if (calendar === null) {
    return (
      <Panel
        kicker="Economic calendar"
        title="Tier-1 radar"
        icon={<Radio aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
        className={className}
        testId="calendar-radar"
        aside={<Pill tone="warning">no calendar</Pill>}
      >
        <NotPublished
          spec="§5"
          field="calendar.json — events, blackouts"
          testId="calendar-missing"
        />
      </Panel>
    );
  }

  const nowMs = reference.getTime();
  const countdowns: Countdown[] = calendar.events
    .filter(isTierOne)
    .map((event) => toCountdown(event, nowMs))
    .filter((entry): entry is Countdown => entry !== null && entry.secondsUntil > -1)
    .sort((left, right) => left.secondsUntil - right.secondsUntil);

  const active = calendar.blackouts.filter((gate) => {
    const start = gate.startUtc === null ? null : Date.parse(gate.startUtc);
    const end = gate.endUtc === null ? null : Date.parse(gate.endUtc);
    return start !== null && end !== null && !Number.isNaN(start) && !Number.isNaN(end) && start <= nowMs && nowMs < end;
  });
  const upcoming = countdowns.slice(0, 5);
  const next = upcoming[0];
  const skew = ageSeconds(calendar.generatedAt, now);
  const stale = skew !== null && skew > STALE_AFTER_SECONDS;

  return (
    <Panel
      kicker="Economic calendar"
      title="Tier-1 radar"
      icon={<CalendarClock aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
      className={className}
      testId="calendar-radar"
      aside={
        <Pill tone={active.length > 0 ? "negative" : "neutral"} testId="gate-count">
          {active.length === 0 ? "no active gate" : `${active.length} active`}
        </Pill>
      }
      footer={
        <div className="flex items-center justify-between gap-4 font-display text-[0.6rem] uppercase tracking-[0.16em] text-white/25">
          <span>Blackout gates from engine.news.calendar</span>
          <span>
            {skew === null ? "no skew reference" : `published ${skew}s ago`}
            {stale && " · stale"}
          </span>
        </div>
      }
    >
      {/* the headline countdown ------------------------------------------- */}
      <div className="rounded-[1.2rem] bg-white/[0.03] p-4 ring-1 ring-white/[0.06]">
        <p className="font-display text-[0.58rem] font-medium uppercase tracking-[0.22em] text-white/35">
          Next Tier-1 release
        </p>
        {next === undefined ? (
          <p className="mt-2 text-sm text-white/40" data-testid="no-next-event">
            No Tier-1 release scheduled in the published window.
          </p>
        ) : (
          <>
            <p className="mt-2 font-display text-[1.05rem] font-semibold tracking-tight text-white">
              {next.event.name}
            </p>
            <p className="mt-3 flex items-baseline gap-2">
              <Clock3 aria-hidden strokeWidth={1.25} className="h-4 w-4 text-cyan-300/70" />
              <span
                data-testid="next-countdown"
                className="font-display text-[1.6rem] font-semibold leading-none tabular-nums text-cyan-300"
              >
                {duration(next.secondsUntil)}
              </span>
              <span className="text-[0.68rem] text-white/35">
                {instant(next.event.timestampUtc, now) ?? "--"}
              </span>
            </p>
            <p className="mt-2 text-[0.68rem] leading-snug text-white/40">
              Blackout opens{" "}
              {formatSeconds(next.event.blackoutBeforeSeconds)} before and stays for{" "}
              {formatSeconds(next.event.blackoutAfterSeconds)} after.
              {next.inBlackout && next.blackoutEndsIn !== null
                ? " A gate is open right now."
                : ""}
            </p>
          </>
        )}
      </div>

      {/* active gates ---------------------------------------------------- */}
      <h3 className="mt-6 font-display text-[0.6rem] font-medium uppercase tracking-[0.22em] text-white/35">
        Active blackout gates
      </h3>
      {active.length === 0 ? (
        <p className="mt-2 text-[0.75rem] text-white/40" data-testid="no-active-gate">
          No blackout is open. New entries are permitted.
        </p>
      ) : (
        <ul className="mt-2 space-y-2" data-testid="active-gates">
          {active.map((gate) => (
            <li
              key={`gate-${gate.name}`}
              className="flex items-center justify-between gap-3 rounded-[1rem] bg-rose-500/[0.07] px-3.5 py-2.5 ring-1 ring-rose-400/20"
            >
              <span className="flex items-center gap-2 text-[0.78rem] text-rose-100/90">
                <span className="h-1.5 w-1.5 rounded-full bg-rose-400" />
                {gate.name}
              </span>
              <span className="font-display text-[0.68rem] tabular-nums text-rose-200/70">
                {duration(Math.max(0, ((Date.parse(gate.endUtc ?? "") || nowMs) - nowMs) / 1000))}{" "}
                left
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* the schedule ---------------------------------------------------- */}
      <h3 className="mt-6 font-display text-[0.6rem] font-medium uppercase tracking-[0.22em] text-white/35">
        Upcoming Tier-1
      </h3>
      {upcoming.length === 0 ? (
        <p className="mt-2 text-[0.75rem] text-white/40" data-testid="no-upcoming">
          Nothing scheduled ahead of the published window.
        </p>
      ) : (
        <ul className="mt-2 divide-y divide-white/[0.06]" data-testid="upcoming-events">
          {upcoming.map((entry) => (
            <li
              key={`event-${entry.event.name}-${entry.event.timestampUtc}`}
              data-testid="upcoming-event"
              className="group/ev flex items-center justify-between gap-4 py-2.5"
            >
              <span className="flex min-w-0 items-center gap-2.5">
                <span
                  className={cx(
                    "h-1.5 w-1.5 shrink-0 rounded-full",
                    entry.event.kind === "RELEASE" ? "bg-amber-400" : "bg-white/25",
                  )}
                />
                <span className="truncate text-[0.8rem] text-white/80">{entry.event.name}</span>
                <span className="shrink-0 font-display text-[0.58rem] uppercase tracking-[0.14em] text-white/25">
                  {entry.event.kind}
                </span>
              </span>
              <span
                className={cx(
                  "shrink-0 font-display text-[0.75rem] font-medium tabular-nums transition-transform duration-700 ease-[cubic-bezier(0.32,0.72,0,1)] group-hover/ev:-translate-y-0.5",
                  entry.inBlackout ? "text-rose-300" : "text-white/55",
                )}
              >
                {entry.secondsUntil <= 0 ? "live" : duration(entry.secondsUntil)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function toCountdown(
  event: CalendarEvent,
  nowMs: number,
): Countdown | null {
  if (event.timestampUtc === null) {
    return null;
  }
  const stamp = Date.parse(event.timestampUtc);
  if (Number.isNaN(stamp)) {
    return null;
  }
  const secondsUntil = Math.round((stamp - nowMs) / 1000);
  const start = stamp - event.blackoutBeforeSeconds * 1000;
  const end = stamp + event.blackoutAfterSeconds * 1000;
  const inBlackout = nowMs >= start && nowMs < end;
  return {
    event,
    secondsUntil,
    inBlackout,
    blackoutEndsIn: inBlackout ? Math.round((end - nowMs) / 1000) : null,
  };
}

function formatSeconds(seconds: number): string {
  if (seconds >= 60) {
    return `${Math.round(seconds / 60)}m`;
  }
  return `${seconds}s`;
}
