/**
 * What a cell says when the engine has not published the field behind it.
 *
 * Phase 6 built this dashboard against `web/src/api/contract.ts`, which mirrors
 * `docs/PHASE_5X_SPEC.md`. Where the spec describes a field the current engine build does
 * not emit, the contract types it nullable and this component renders in its place.
 *
 * The alternative was a blank cell, and a blank cell is a lie: an operator cannot tell a
 * stop-loss of zero from a stop-loss nobody has published, and only one of those is safe
 * to assume. So the gap is stated, on screen, every time.
 *
 * The spec reference is not decoration. It is the difference between "this value is
 * missing" and "here is the ticket that says who is publishing it and why not yet".
 */

import type { ReactElement } from "react";
import { NOT_PUBLISHED } from "../api/contract";
import { CircleSlash } from "lucide-react";

export interface NotPublishedProps {
  /** The snapshot field that is absent, e.g. `trades[].stop_loss`. */
  readonly field?: string;
  /** The section of `docs/PHASE_5X_SPEC.md` that specifies it, e.g. `§2`. */
  readonly spec?: string;
  /** Render the one-line form, for a table cell. */
  readonly compact?: boolean;
  readonly testId?: string;
}

export function NotPublished({
  field,
  spec,
  compact = false,
  testId,
}: NotPublishedProps): ReactElement {
  const reference = spec === undefined ? undefined : `docs/PHASE_5X_SPEC.md ${spec}`;
  const explanation =
    `This engine build does not publish this value. It is not zero and not an empty result.` +
    (reference === undefined ? "" : ` Specified in ${reference}.`);

  if (compact) {
    return (
      <span
        data-testid={testId ?? "not-published-compact"}
        data-not-published="true"
        title={explanation}
        className="inline-flex items-center gap-1.5 font-display text-[0.62rem] uppercase tracking-[0.14em] text-white/25"
      >
        <CircleSlash aria-hidden strokeWidth={1.25} className="h-3 w-3" />
        {NOT_PUBLISHED}
      </span>
    );
  }

  return (
    <div
      data-testid={testId ?? "not-published"}
      data-not-published="true"
      title={explanation}
      className="flex items-start gap-3 rounded-[1.1rem] bg-white/[0.02] p-4 ring-1 ring-white/[0.06]"
    >
      <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full bg-white/[0.05] text-white/40 ring-1 ring-white/10">
        <CircleSlash aria-hidden strokeWidth={1.25} className="h-3.5 w-3.5" />
      </span>
      <div>
        <p className="font-display text-[0.62rem] font-medium uppercase tracking-[0.2em] text-white/40">
          {NOT_PUBLISHED}
        </p>
        {field !== undefined && (
          <p className="mt-1 font-display text-[0.7rem] text-white/45">{field}</p>
        )}
        {reference !== undefined && (
          <p className="mt-1 text-[0.68rem] leading-snug text-white/30">
            Specified in {reference}. It is not zero, and it is not an empty result.
          </p>
        )}
      </div>
    </div>
  );
}

/** Render `value` when it is published, and the sentinel when it is not.

The sentinel always keeps its own `data-not-published` marker, so a test can count the gaps
in a table without having to know which column each one came from.
*/
export function OrNotPublished({
  value,
  field,
  spec,
  testId,
}: {
  readonly value: string | null;
  readonly field?: string;
  readonly spec?: string;
  readonly testId?: string;
}): ReactElement {
  if (value === null) {
    return <NotPublished field={field} spec={spec} compact />;
  }
  return (
    <span className="tabular-nums text-white/80" data-testid={testId}>
      {value}
    </span>
  );
}
