/**
 * Test helpers: an API stub and the renderers built on it.
 *
 * `stubApi` replaces `fetch` with a router keyed by the exact relative URL the dashboard
 * asked for. That is deliberate: if the client ever starts building an absolute URL, the
 * stub misses, the payload comes back `null`, and the assertion about relative endpoints
 * fails. A permissive stub would hide exactly the regression it is there to catch.
 */

import type { ReactElement } from "react";
import { render } from "@testing-library/react";
import { vi } from "vitest";
import { ENDPOINTS, type EngineSnapshot } from "../api/contract";
import { App } from "../App";

/** One canned API answer, by endpoint. */
export interface ApiStub {
  readonly status?: Record<string, unknown> | null;
  readonly trades?: Record<string, unknown> | null;
  readonly metrics?: Record<string, unknown> | null;
  readonly health?: Record<string, unknown> | null;
  readonly setups?: Record<string, unknown> | null;
  readonly calendar?: Record<string, unknown> | null;
}

/** Every endpoint the dashboard is allowed to ask for. */
export const API_CALLS: readonly string[] = Object.values(ENDPOINTS);

interface Answer {
  readonly ok: boolean;
  readonly payload: unknown;
}

function answerTable(stub: ApiStub): Map<string, Answer> {
  const entries: ReadonlyArray<readonly [string, Record<string, unknown> | null | undefined]> = [
    [ENDPOINTS.status, stub.status],
    [ENDPOINTS.trades, stub.trades],
    [ENDPOINTS.metrics, stub.metrics],
    [ENDPOINTS.health, stub.health],
    [ENDPOINTS.setups, stub.setups],
    [ENDPOINTS.calendar, stub.calendar],
  ];
  const table = new Map<string, Answer>();
  for (const [endpoint, payload] of entries) {
    if (payload === undefined) {
      // Absent from the stub entirely: the endpoint does not exist on this engine build.
      continue;
    }
    table.set(endpoint, { ok: payload !== null, payload: payload ?? {} });
  }
  return table;
}

export interface StubbedApi {
  /** Every URL the dashboard asked for, in order, re-read at call time. */
  readonly urls: () => string[];
  /** The stub handler itself, for tests that change it mid-flight. */
  readonly handler: ReturnType<typeof vi.fn>;
}

/**
 * Install a fetch stub.
 *
 * An endpoint explicitly mapped to `null` answers 503, which is what `api/main.py` does
 * for a snapshot that has not been published yet. An endpoint left off the map answers 404,
 * which is what a Phase 5.x endpoint does on an engine build that does not have it.
 */
export function stubApi(stub: ApiStub): StubbedApi {
  const table = answerTable(stub);
  const handler = vi.fn(async (input: unknown): Promise<unknown> => {
    const url = typeof input === "string" ? input : String(input);
    const answer = table.get(url);
    if (answer === undefined) {
      return { ok: false, status: 404, json: async () => ({}) };
    }
    return {
      ok: answer.ok,
      status: answer.ok ? 200 : 503,
      json: async () => answer.payload,
    };
  });

  vi.stubGlobal("fetch", handler);
  return {
    urls: () => handler.mock.calls.map((call) => String(call[0])),
    handler,
  };
}

/** Render an element with the given API already stubbed. */
export function renderWithApi(
  element: ReactElement,
  stub: ApiStub,
): ReturnType<typeof render> & { api: StubbedApi } {
  const api = stubApi(stub);
  return { ...render(element), api };
}

/** Render the whole `App` against a fixed snapshot, with no polling involved. */
export function renderAppWith(snapshot: EngineSnapshot): ReturnType<typeof render> {
  return render(<App snapshot={snapshot} />);
}
