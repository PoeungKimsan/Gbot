/**
 * A clock for the countdowns, and only the countdowns.
 *
 * The whole dashboard renders from a fetched snapshot; the only things that move on their
 * own are the calendar countdown and the payload age in the header. Both are derived from
 * this, so there is exactly one timer in the app and it is trivially testable.
 *
 * The tick is in milliseconds and defaults to one second, which is the resolution the
 * countdowns display. It is cleared on unmount, so a navigating browser does not accumulate
 * a set of orphaned intervals — which is the failure mode of a React app that re-renders on
 * every poll.
 */

import { useEffect, useState } from "react";

export function useNow(intervalMs = 1_000): Date {
  const [now, setNow] = useState<Date>(() => new Date());

  useEffect(() => {
    const id = setInterval(() => {
      setNow(new Date());
    }, intervalMs);
    return () => {
      clearInterval(id);
    };
  }, [intervalMs]);

  return now;
}
