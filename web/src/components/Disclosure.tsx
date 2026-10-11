/**
 * The mandatory disclosure, rendered verbatim and directly adjacent to performance.
 *
 * `web/src/content/disclosure.txt` is the only source. It is read at build time (see
 * `src/content/disclosure.ts`), and the build fails outright if the file is missing or
 * empty — `vite.config.ts` installs a guard plugin that says so by name before any module
 * is resolved.
 *
 * The rendering is verbatim by construction: the text is one React text node inside an
 * element with `white-space: pre-wrap`. There is no markdown pass, no truncation, no
 * "read more" fold, and no ellipsis. A disclosure a user has to expand is a disclosure a
 * user does not read.
 *
 * This component is rendered by **every** performance panel in the dashboard — the hero
 * panel, the expectancy island, and any panel that introduces one later. That adjacency is
 * asserted in `src/components/Disclosure.test.tsx` for both panels and for the assembled
 * app, so a panel that grows a P&L figure without growing its disclosure fails the suite.
 */

import type { ReactElement } from "react";
import { DISCLOSURE_HEADINGS, DISCLOSURE_SOURCE, DISCLOSURE_TEXT } from "../content/disclosure";
import { Panel } from "./Panel";
import { ScrollText } from "lucide-react";

export interface DisclosureProps {
  /** Who the notice is attached to, e.g. `Realized performance`. */
  readonly context?: string;
  /** Render the notice small and inline rather than as a titled block. */
  readonly compact?: boolean;
  readonly testId?: string;
}

export function Disclosure({ context, compact = false, testId }: DisclosureProps): ReactElement {
  const heading =
    context === undefined
      ? "Risk & Simulation Disclosure"
      : `Risk & Simulation Disclosure — ${context}`;

  if (compact) {
    return (
      <div
        data-testid={testId ?? "disclosure-compact"}
        data-disclosure-verbatim="true"
        className="mt-4 rounded-[1.1rem] bg-amber-500/[0.06] p-4 ring-1 ring-amber-400/20"
      >
        <p className="font-display text-[0.6rem] font-medium uppercase tracking-[0.22em] text-amber-300/80">
          Mandatory disclosure · simulated / paper trading
        </p>
        <p
          data-testid="disclosure-text"
          className="mt-2 whitespace-pre-wrap text-[0.68rem] leading-relaxed text-white/55"
        >
          {DISCLOSURE_TEXT}
        </p>
      </div>
    );
  }

  return (
    <Panel
      kicker="Mandatory notice"
      title={heading}
      testId={testId ?? "disclosure"}
      icon={<ScrollText aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
      aside={
        <span className="font-display text-[0.6rem] uppercase tracking-[0.18em] text-white/30">
          {DISCLOSURE_HEADINGS.length} sections
        </span>
      }
      className="border-0"
    >
      <p
        data-testid="disclosure-text"
        data-disclosure-verbatim="true"
        className="whitespace-pre-wrap text-[0.78rem] leading-relaxed text-white/65"
      >
        {DISCLOSURE_TEXT}
      </p>
      <p className="mt-4 font-display text-[0.62rem] uppercase tracking-[0.18em] text-white/25">
        Rendered verbatim from {DISCLOSURE_SOURCE} · appears adjacent to every performance
        panel on this dashboard
      </p>
    </Panel>
  );
}

/** The sentinel a panel shows if the disclosure somehow failed to load. */
export function DisclosureMissing(): ReactElement {
  return (
    <p
      data-testid="disclosure-missing"
      className="rounded-[1.1rem] bg-rose-500/10 p-4 text-[0.72rem] text-rose-300 ring-1 ring-rose-400/25"
    >
      The mandatory disclosure could not be rendered. This build must not be deployed.
    </p>
  );
}
