/**The display boundary: turn wire values into text.

This is where a price stops being a tick count and starts being something a person reads,
so it is the only place in the web layer allowed to be sloppy about types — and it still
isn't. Money is formatted through `lib/decimal.ts`, which multiplies and shifts on `BigInt`
and never produces a float, so `$2009.75` is `$2009.75` and not `$2009.7499999999998`.

The two things here that are genuinely floats are the ones the engine already made floats:
`metrics.json`'s win rate, profit factor and bootstrap bounds come from
`engine/metrics/performance.py`, the module `AGENTS.md` 2.1 exempts as the reporting
boundary. They are formatted, never computed with.
*/

import { formatDecimal, formatDecimalString, parseDecimal, ticksToDecimal } from "./decimal";
/** A decimal string, or `null` when the engine has not published the field. */
export function money(value: string | null, options?: { signed?: boolean }): string | null {
  if (value === null) {
    return null;
  }
  return formatDecimalString(value, { grouping: true, signed: options?.signed ?? false });
}

/** Format a decimal string with exactly two fraction digits and a currency prefix. */
export function currency(value: string | null, options?: { signed?: boolean }): string | null {
  const body = money(value, options);
  if (body === null) {
    return null;
  }
  // The sign has to come off *both* ways, or a signed gain reads as "$+360.00".
  const sign = body.startsWith("-") ? "-" : body.startsWith("+") ? "+" : "";
  const bare = body.slice(sign.length);
  return `${sign}$${bare}`;
}

/** A price in integer ticks, rendered against the venue's tick size.

Returns `null` when the tick size is unknown: ticks without a tick size are an unreadable
integer, and printing `200730` where a price belongs is worse than printing nothing.
*/
export function price(ticks: number | null, tickSize: string | null): string | null {
  if (ticks === null || tickSize === null) {
    return null;
  }
  const unit = parseDecimal(tickSize);
  if (unit === null) {
    return null;
  }
  const value = ticksToDecimal(ticks, unit);
  if (value === null) {
    return null;
  }
  return formatDecimal(value, { grouping: true, fraction: 2 });
}

/** A realized R multiple, signed, with the unit attached. */
export function rMultiple(value: string | null): string | null {
  if (value === null) {
    return null;
  }
  const body = formatDecimalString(value, { grouping: false, signed: true, fraction: 2 });
  return body === null ? null : `${body} R`;
}

/** A bootstrap bound, signed to two decimals. */
export function signedR(value: number | null): string | null {
  if (value === null) {
    return null;
  }
  return `${value < 0 ? "-" : "+"}${Math.abs(value).toFixed(2)} R`;
}

/** A percentage from the reporting module's float. `INSUFFICIENT DATA` has no win rate. */
export function percent(value: number | null, digits = 1): string | null {
  if (value === null) {
    return null;
  }
  return `${(value * 100).toFixed(digits)}%`;
}

/** A profit factor, or `null` when the ratio is undefined rather than infinite. */
export function factor(value: number | null): string | null {
  return value === null ? null : `${value.toFixed(2)}x`;
}

/** A large count with thin separators. */
export function count(value: number): string {
  return value.toLocaleString("en-US");
}

/** `1h 02m 03s` down to `12s`. */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "--";
  }
  const total = Math.floor(seconds);
  const days = Math.floor(total / 86_400);
  if (days > 0) {
    return `${days}d ${String(Math.floor((total % 86_400) / 3_600)).padStart(2, "0")}h`;
  }
  const hours = Math.floor(total / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}h ${String(minutes).padStart(2, "0")}m ${String(secs).padStart(2, "0")}s`;
  }
  if (minutes > 0) {
    return `${minutes}m ${String(secs).padStart(2, "0")}s`;
  }
  return `${secs}s`;
}

/** `07:39:52 UTC`, or the date too when the instant is not today. */
export function instant(iso: string | null, now: Date = new Date()): string | null {
  if (iso === null) {
    return null;
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  const clock = parsed.toISOString().slice(11, 19);
  const sameDay = parsed.toISOString().slice(0, 10) === now.toISOString().slice(0, 10);
  return sameDay ? `${clock} UTC` : `${parsed.toISOString().slice(0, 10)} ${clock}z`;
}

/** Whole seconds between two instants, or `null` when either is unreadable. */
export function ageSeconds(iso: string | null, now: Date = new Date()): number | null {
  if (iso === null) {
    return null;
  }
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) {
    return null;
  }
  return Math.max(0, Math.round((now.getTime() - parsed) / 1000));
}

/** `12s ago`, `4m ago`, `2h ago`. */
export function ago(seconds: number | null): string | null {
  if (seconds === null) {
    return null;
  }
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours}h ago`;
  }
  return `${Math.floor(hours / 24)}d ago`;
}

/** The first eight and last four hex digits of a SHA-256, with the full value on hover. */
export function shortHash(hash: string): string {
  if (hash.length <= 16) {
    return hash;
  }
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}
