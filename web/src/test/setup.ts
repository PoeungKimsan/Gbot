/**
 * Vitest setup.
 *
 * `@testing-library/jest-dom` adds the DOM matchers (`toBeInTheDocument`, `toHaveTextContent`)
 * and, importantly, its `vitest` entry point re-exports them against Vitest's `expect`
 * rather than Jest's, which is how a `globals: true` project selects the right one.
 *
 * `matchMedia` is stubbed because jsdom does not implement it and React's `useSyncExternalStore`
 * (reachable transitively through some libraries) will throw on first access. The dashboard
 * itself never queries it — its responsive behaviour is CSS `lg:` breakpoints, chosen
 * deliberately so there is no JS media-query state to keep in sync or to test.
 */

import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

if (typeof window !== "undefined" && window.matchMedia === undefined) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList,
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
