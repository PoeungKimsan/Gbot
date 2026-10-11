/**
 * Poll the read-only API, and never let it take the dashboard down.
 *
 * `api/main.py` caches every snapshot response for ten seconds, so the default poll is
 * eleven: one second past the cache lifetime, which is the fastest a poll can produce new
 * data without being wasted work.
 *
 * An endpoint that answers 503 (`state.json` not published yet) or 404 (a Phase 5.x
 * endpoint this engine build does not have) is not an error. `fetchSnapshot` maps both to
 * `null`, the views render their sentinels, and the poll keeps going. A dashboard that
 * hard-fails on a missing snapshot is a dashboard that goes white the moment the engine
 * restarts its publisher.
 */

import { useCallback, useEffect, useState } from "react";
import { fetchSnapshot } from "../api/client";
import type { EngineSnapshot } from "../api/contract";

/** Eleven seconds: one past the API's own ten-second cache lifetime. */
export const POLL_INTERVAL_MS = 11_000;

export interface UseEngineSnapshotResult {
  readonly snapshot: EngineSnapshot | null;
  /** Set only when this hook's own plumbing failed; network answers do not set it. */
  readonly error: string | null;
  readonly loading: boolean;
  readonly refresh: () => void;
}

export function useEngineSnapshot(intervalMs: number = POLL_INTERVAL_MS): UseEngineSnapshotResult {
  const [snapshot, setSnapshot] = useState<EngineSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [generation, setGeneration] = useState(0);

  const refresh = useCallback(() => {
    setGeneration((value) => value + 1);
  }, []);

  useEffect(() => {
    let live = true;
    setLoading(true);

    const load = async (): Promise<void> => {
      try {
        const next = await fetchSnapshot();
        if (!live) {
          return;
        }
        setSnapshot(next);
        setError(null);
      } catch (cause: unknown) {
        if (!live) {
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        if (live) {
          setLoading(false);
        }
      }
    };

    void load();
    const id = setInterval(() => void load(), intervalMs);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [intervalMs, generation]);

  return { snapshot, error, loading, refresh };
}
