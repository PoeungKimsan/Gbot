/**
 * The double-bezel card architecture, and the small primitives that sit inside it.
 *
 * Every panel in this dashboard is a *nested* surface rather than a bordered box: an outer
 * frame with a soft radial fill, a concentric hairline ring, and an inner core inset by a
 * calibrated radius (1.75rem outer minus 0.375rem padding) that carries its own top
 * highlight. That inner highlight is the whole trick — it is what makes the card read as
 * a physical object sitting above the OLED black rather than a div with a border.
 *
 * Motion is transform and opacity only, on a 700ms cubic-bezier(0.32, 0.72, 0, 1) curve.
 * Nothing animates a layout property, because animating width on a grid of panels is how
 * a dashboard ends up looking cheap at 120Hz.
 */

import type { ReactElement, ReactNode } from "react";

/** Join class names, dropping the empty ones. */
export function cx(...values: ReadonlyArray<string | false | null | undefined>): string {
  return values.filter((value): value is string => typeof value === "string" && value !== "")
    .join(" ");
}

/** The outer frame plus inner core, ready for a panel's own content. */
export function Banshell({
  children,
  className,
  innerClassName,
  ...rest
}: {
  readonly children: ReactNode;
  readonly className?: string;
  readonly innerClassName?: string;
  readonly "data-testid"?: string;
  readonly role?: string;
  readonly "aria-label"?: string;
}): ReactElement {
  return (
    <div
      data-testid={rest["data-testid"]}
      role={rest.role}
      aria-label={rest["aria-label"]}
      className={cx(
        "frame group relative rounded-[1.75rem] p-1.5 ring-1 ring-white/10",
        "bg-[radial-gradient(120%_140%_at_15%_0%,rgba(0,240,255,0.07),transparent_55%),radial-gradient(120%_140%_at_100%_100%,rgba(16,185,129,0.06),transparent_60%)]",
        "bg-white/[0.035] backdrop-blur-2xl transition-transform duration-700 ease-[cubic-bezier(0.32,0.72,0,1)]",
        "hover:ring-white/20",
        className,
      )}
    >
      <div
        className={cx(
          "core relative flex h-full flex-col rounded-[1.375rem] bg-[#08090b]/80 ring-1 ring-white/[0.06]",
          "shadow-[inset_0_1px_1px_rgba(255,255,255,0.12)]",
          innerClassName,
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** A panel: a bezel around a labelled, optional icon, content, and an optional footer. */
export function Panel({
  kicker,
  title,
  icon,
  aside,
  footer,
  children,
  className,
  testId,
}: {
  readonly kicker?: string;
  readonly title?: string;
  readonly icon?: ReactNode;
  readonly aside?: ReactNode;
  readonly footer?: ReactNode;
  readonly children: ReactNode;
  readonly className?: string;
  readonly testId?: string;
}): ReactElement {
  return (
    <Banshell className={className} data-testid={testId}>
      {(title !== undefined || kicker !== undefined) && (
        <header className="flex items-start justify-between gap-4 px-6 pt-5 pb-3">
          <div className="flex items-start gap-3">
            {icon !== undefined && (
              <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-white/[0.06] text-white/70 ring-1 ring-white/10">
                {icon}
              </span>
            )}
            <div>
              {kicker !== undefined && (
                <p className="font-display text-[0.62rem] font-medium uppercase tracking-[0.22em] text-white/40">
                  {kicker}
                </p>
              )}
              {title !== undefined && (
                <h2 className="font-display text-[0.95rem] font-semibold leading-tight tracking-tight text-white">
                  {title}
                </h2>
              )}
            </div>
          </div>
          {aside !== undefined && <div className="shrink-0">{aside}</div>}
        </header>
      )}
      <div className="flex-1 px-6 pb-5">{children}</div>
      {footer !== undefined && (
        <footer className="border-t border-white/[0.06] px-6 py-4">{footer}</footer>
      )}
    </Banshell>
  );
}

/** A hero KPI tile: label, big value, unit, and a baseline it can be read against. */
export function StatTile({
  label,
  value,
  unit,
  baseline,
  tone = "neutral",
  testId,
}: {
  readonly label: string;
  readonly value: string;
  readonly unit?: string;
  readonly baseline?: string;
  readonly tone?: "neutral" | "positive" | "negative" | "warning";
  readonly testId?: string;
}): ReactElement {
  const valueTone =
    tone === "positive"
      ? "text-emerald-300"
      : tone === "negative"
        ? "text-rose-300"
        : tone === "warning"
          ? "text-amber-300"
          : "text-white";
  return (
    <div
      data-testid={testId}
      className="group/tile flex flex-col gap-1.5 rounded-[1.2rem] bg-white/[0.03] p-4 ring-1 ring-white/[0.07] transition-transform duration-700 ease-[cubic-bezier(0.32,0.72,0,1)] hover:bg-white/[0.05] group-active:scale-[0.99]"
    >
      <p className="font-display text-[0.6rem] font-medium uppercase tracking-[0.2em] text-white/40">
        {label}
      </p>
      <p className="flex items-baseline gap-1.5">
        <span
          className={cx(
            "font-display text-[1.75rem] font-semibold leading-none tracking-tight tabular-nums",
            valueTone,
          )}
        >
          {value}
        </span>
        {unit !== undefined && (
          <span className="font-display text-xs font-medium text-white/40">{unit}</span>
        )}
      </p>
      {baseline !== undefined && (
        <p className="text-[0.7rem] leading-snug text-white/35">{baseline}</p>
      )}
    </div>
  );
}

/** A circular or pill enclosure for a secondary action or a status marker. */
export function Pill({
  children,
  tone = "neutral",
  className,
  testId,
}: {
  readonly children: ReactNode;
  readonly tone?: "neutral" | "positive" | "negative" | "warning" | "accent";
  readonly className?: string;
  readonly testId?: string;
}): ReactElement {
  const tones: Record<string, string> = {
    neutral: "bg-white/[0.05] text-white/70 ring-white/10",
    positive: "bg-emerald-500/10 text-emerald-300 ring-emerald-400/25",
    negative: "bg-rose-500/10 text-rose-300 ring-rose-400/25",
    warning: "bg-amber-500/10 text-amber-300 ring-amber-400/25",
    accent: "bg-cyan-500/10 text-cyan-300 ring-cyan-400/25",
  };
  return (
    <span
      data-testid={testId}
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-display text-[0.62rem] font-medium uppercase tracking-[0.16em] ring-1",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** The small live indicator. */
export function Dot({
  tone = "neutral",
  pulse = false,
}: {
  readonly tone?: "neutral" | "positive" | "negative" | "warning" | "accent";
  readonly pulse?: boolean;
}): ReactElement {
  const tones: Record<string, string> = {
    neutral: "bg-white/50",
    positive: "bg-emerald-400",
    negative: "bg-rose-400",
    warning: "bg-amber-400",
    accent: "bg-cyan-400",
  };
  return (
    <span className="relative grid h-2.5 w-2.5 place-items-center">
      <span className={cx("h-2 w-2 rounded-full", tones[tone])} />
      {pulse && (
        <span
          className={cx(
            "absolute h-2.5 w-2.5 animate-[ping_2.4s_cubic-bezier(0.32,0.72,0,1)_infinite] rounded-full opacity-60",
            tones[tone],
          )}
        />
      )}
    </span>
  );
}

/** A labelled key/value row, the unit of every data table in this dashboard. */
export function Row({
  label,
  value,
  hint,
  testId,
}: {
  readonly label: string;
  readonly value: ReactNode;
  readonly hint?: string;
  readonly testId?: string;
}): ReactElement {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2" data-testid={testId}>
      <span className="text-[0.72rem] uppercase tracking-[0.14em] text-white/40">{label}</span>
      <span className="text-right font-display text-sm font-medium tabular-nums text-white/85">
        {value}
        {hint !== undefined && (
          <span className="ml-2 align-middle text-[0.68rem] text-white/30">{hint}</span>
        )}
      </span>
    </div>
  );
}
