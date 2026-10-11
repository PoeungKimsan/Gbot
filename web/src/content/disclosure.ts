/**
 * The mandatory disclosure, read off disk at build time and rendered verbatim.
 *
 * Two independent guards, because either one alone is a gap:
 *
 * 1. `vite.config.ts` installs `xauusd-disclosure-guard`, which reads this file before a
 *    single module is resolved and fails the build with a named error if it is missing or
 *    empty.
 * 2. This module refuses to load if the text it was handed is not usable. Vite inlines
 *    `?raw` imports as strings, so a build that somehow got a `null` or an empty file fails
 *    at import time rather than at the moment an operator needed to read it.
 *
 * The text is never transformed, trimmed, collapsed or re-parsed. `web/src/content/
 * disclosure.txt` is the only source, and `Disclosure.tsx` renders exactly these bytes.
 */

import RAW from "./disclosure.txt?raw";

/** Where the verbatim text lives, named in the error messages an operator will read. */
export const DISCLOSURE_SOURCE = "web/src/content/disclosure.txt";

/** Validate and hand back the disclosure.
 *
 * Exported separately from the module constant so the failure path is testable without
 * having to manipulate files on disk during a run.
 */
export function readDisclosure(raw: unknown): string {
  if (typeof raw !== "string") {
    throw new Error(
      `${DISCLOSURE_SOURCE} did not load as text (got ${typeof raw}); ` +
        "the mandatory disclosure cannot be rendered.",
    );
  }
  if (raw.trim() === "") {
    throw new Error(
      `${DISCLOSURE_SOURCE} is empty; the mandatory disclosure cannot be rendered. ` +
        "A dashboard that shows performance without its disclosure is not shippable.",
    );
  }
  return raw;
}

/** The disclosure, exactly as it appears on disk. */
export const DISCLOSURE_TEXT: string = readDisclosure(RAW);

/** The section headings, in file order, so a panel can link to one of them. */
export const DISCLOSURE_HEADINGS: readonly string[] = DISCLOSURE_TEXT.split("\n")
  .filter((line) => /^\s*\d+\.\s+\S/.test(line))
  .map((line) => line.trim());
