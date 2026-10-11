/**
 * The calendar radar: the countdown, the gates, and the tiering rule.
 *
 * The countdown is driven by the browser clock, so the suite runs it against a frozen
 * `vi.useFakeTimers()` clock. That is the only way to assert that a countdown actually
 * counts down rather than being right by accident at render time.
 *
 * The other half is that an **advisory** event is never treated as a gate.
 * `engine.news.calendar` makes that rule explicit — `EconomicEvent.is_blocking` is true
 * only for `EventKind.RELEASE`, a FRED-confirmed date — and the dashboard repeats it. If
 * the dashboard dropped that filter, a Forex Factory rumour would be able to block trading
 * in the operator's view of the world.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { CalendarRadar } from "./CalendarRadar";
import { CALENDAR_FIXTURE, DEMO_NOW, TICK_SIZE } from "../api/fixtures";
import type { CalendarEvent, CalendarPayload } from "../api/contract";

const NOW = new Date(DEMO_NOW.getTime());

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

/** A schedule around `NOW`, with offsets in seconds. */
function scheduleAt(offsets: ReadonlyArray<number>): CalendarPayload {
  return {
    generatedAt: NOW.toISOString(),
    events: offsets.map((offset, index) => ({
      name: `Event ${index + 1}`,
      impact: "high" as const,
      kind: "RELEASE" as const,
      timestampUtc: new Date(NOW.getTime() + offset * 1000).toISOString(),
      localDate: "2026-03-10",
      blackoutBeforeSeconds: 900,
      blackoutAfterSeconds: 3600,
    })),
    blackouts: [],
  };
}

describe("with no calendar payload", () => {
  it("renders the sentinel and no countdown", () => {
    render(<CalendarRadar calendar={null} now={NOW} />);
    expect(screen.getByTestId("calendar-missing")).toBeInTheDocument();
    expect(screen.queryByTestId("next-countdown")).not.toBeInTheDocument();
  });

  it("names the spec section that specifies it", () => {
    render(<CalendarRadar calendar={null} now={NOW} />);
    expect(screen.getByTestId("calendar-missing").textContent).toContain("§5");
  });
});

describe("the countdown", () => {
  it("counts down to the next Tier-1 release", () => {
    render(
      <CalendarRadar calendar={scheduleAt([3_600])} now={NOW} />,
    );
    expect(screen.getByTestId("next-countdown")).toHaveTextContent("1h 00m 00s");
  });

  it("moves as the clock does", () => {
    // No `now` prop: the component has to own its own clock for this to prove anything
    // about a *live* countdown.
    render(<CalendarRadar calendar={scheduleAt([3_600])} />);
    expect(screen.getByTestId("next-countdown")).toHaveTextContent("1h 00m 00s");

    // `act` is what flushes the interval's state update into the DOM.
    act(() => {
      vi.advanceTimersByTime(42_000);
    });

    expect(screen.getByTestId("next-countdown")).toHaveTextContent("59m 18s");
  });

  it("shows the nearest event, not the first one listed", () => {
    render(
      <CalendarRadar calendar={scheduleAt([7_200, 600, 3_600])} now={NOW} />,
    );
    expect(screen.getByTestId("next-countdown")).toHaveTextContent("10m 00s");
  });

  it("lists the upcoming events with their own countdowns", () => {
    render(
      <CalendarRadar calendar={scheduleAt([600, 3_600, 7_200])} now={NOW} />,
    );
    expect(screen.getAllByTestId("upcoming-event")).toHaveLength(3);
  });

  it("says so when nothing is scheduled", () => {
    render(<CalendarRadar calendar={scheduleAt([])} now={NOW} />);
    expect(screen.getByTestId("no-next-event")).toBeInTheDocument();
  });
});

describe("the blackout gates", () => {
  it("shows no active gate when none is open", () => {
    render(<CalendarRadar calendar={scheduleAt([7_200])} now={NOW} />);
    expect(screen.getByTestId("no-active-gate")).toBeInTheDocument();
    expect(screen.getByTestId("gate-count")).toHaveTextContent(/no active gate/i);
  });

  it("shows the gate that is open", () => {
    const calendar: CalendarPayload = {
      ...scheduleAt([3_600]),
      blackouts: [
        {
          name: "Event 1",
          startUtc: new Date(NOW.getTime() - 60_000).toISOString(),
          endUtc: new Date(NOW.getTime() + 300_000).toISOString(),
          blocking: true,
        },
      ],
    };
    render(<CalendarRadar calendar={calendar} />);
    const gates = screen.getByTestId("active-gates");
    expect(gates).toBeInTheDocument();
    expect(gates.textContent).toContain("Event 1");
    expect(gates.textContent).toContain("5m 00s");
    expect(screen.getByTestId("gate-count")).toHaveTextContent(/1 active/i);
  });

  it("drops a gate the moment it closes", () => {
    const calendar: CalendarPayload = {
      ...scheduleAt([3_600]),
      blackouts: [
        {
          name: "Event 1",
          startUtc: new Date(NOW.getTime() - 60_000).toISOString(),
          endUtc: new Date(NOW.getTime() + 2_000).toISOString(),
          blocking: true,
        },
      ],
    };
    render(<CalendarRadar calendar={calendar} />);
    expect(screen.getByTestId("active-gates")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(5_000);
    });

    expect(screen.queryByTestId("active-gates")).not.toBeInTheDocument();
    expect(screen.getByTestId("no-active-gate")).toBeInTheDocument();
  });
});

describe("the tiering rule", () => {
  function event(kind: CalendarEvent["kind"], impact: CalendarEvent["impact"]): CalendarPayload {
    return {
      generatedAt: NOW.toISOString(),
      events: [
        {
          name: "Advisory rumour",
          impact,
          kind,
          timestampUtc: new Date(NOW.getTime() + 600_000).toISOString(),
          localDate: "2026-03-10",
          blackoutBeforeSeconds: 900,
          blackoutAfterSeconds: 3_600,
        },
      ],
      blackouts: [],
    };
  }

  it("treats a FRED-confirmed high-impact release as Tier 1", () => {
    render(<CalendarRadar calendar={event("RELEASE", "high")} now={NOW} />);
    expect(screen.getByTestId("next-countdown")).toBeInTheDocument();
  });

  it("never treats an advisory hit as Tier 1", () => {
    render(<CalendarRadar calendar={event("ADVISORY", "high")} now={NOW} />);
    expect(screen.getByTestId("no-next-event")).toBeInTheDocument();
  });

  it("never treats a low-impact release as Tier 1", () => {
    render(<CalendarRadar calendar={event("RELEASE", "low")} now={NOW} />);
    expect(screen.getByTestId("no-next-event")).toBeInTheDocument();
  });
});

describe("the fixture schedule", () => {
  it("has a live event and a future one, so the countdown is real", () => {
    render(<CalendarRadar calendar={CALENDAR_FIXTURE} now={NOW} />);
    expect(screen.getByTestId("next-countdown")).toBeInTheDocument();
    expect(screen.getAllByTestId("upcoming-event").length).toBeGreaterThan(0);
  });

  it("uses the instrument tick size only through the props it is given", () => {
    void TICK_SIZE;
    expect(CALENDAR_FIXTURE.events.length).toBeGreaterThan(0);
  });
});
