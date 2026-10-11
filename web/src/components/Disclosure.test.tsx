/**
 * The disclosure: verbatim, adjacent, and enforced.
 *
 * Three properties carry the weight.
 *
 * **It is verbatim.** `web/src/content/disclosure.txt` is read off disk in this test and
 * compared byte for byte against the module that renders it. No trim, no collapse, no
 * markdown pass. A disclosure that has been edited into a summary is a disclosure that has
 * been removed.
 *
 * **It is adjacent to every performance panel.** Both the hero panel and the expectancy
 * island carry the notice, and each is asserted to carry it *inside its own subtree* — a
 * notice rendered somewhere else on the page is not adjacent to anything.
 *
 * **It refuses to vanish.** `readDisclosure` is the guard, and it is exercised against the
 * empty and the absent cases directly, because a build-time check nobody has run is a
 * build-time check nobody can trust.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { DISCLOSURE_TEXT, readDisclosure } from "../content/disclosure";
import { Disclosure } from "./Disclosure";
import { ExpectancyIsland, PerformancePanel } from "./PerformancePanel";
import { render, screen, within } from "@testing-library/react";
import { METRICS_COMPLETE, METRICS_INSUFFICIENT, fullSnapshot } from "../api/fixtures";
import { renderAppWith } from "../test/render";

/**
 * `import.meta.url` is an `http://` URL under jsdom, so `fileURLToPath` refuses it. Vitest
 * runs with the web root as its working directory, which is where the source lives.
 */
const SOURCE = path.resolve(process.cwd(), "src/content/disclosure.txt");

/** The file, exactly as it sits on disk. */
const ON_DISK = readFileSync(SOURCE, "utf-8");

describe("the disclosure text itself", () => {
  it("is the file on disk, byte for byte", () => {
    expect(DISCLOSURE_TEXT).toBe(ON_DISK);
  });

  it("is not empty and is long enough to be a notice", () => {
    expect(DISCLOSURE_TEXT.trim()).not.toBe("");
    expect(DISCLOSURE_TEXT.length).toBeGreaterThan(500);
  });

  it("states that the system is simulated", () => {
    // The one sentence that must never be lost.
    expect(DISCLOSURE_TEXT).toContain("SIMULATED / PAPER TRADING");
    expect(DISCLOSURE_TEXT).toContain("no real money is at risk");
  });

  it("names the N < 30 rule it will show", () => {
    expect(DISCLOSURE_TEXT).toContain("INSUFFICIENT DATA (<30 TRADES)");
  });
});

describe("readDisclosure", () => {
  it("refuses a non-string", () => {
    expect(() => readDisclosure(null)).toThrow(/did not load as text/);
  });

  it("refuses an empty file", () => {
    expect(() => readDisclosure("")).toThrow(/is empty/);
  });

  it("refuses whitespace only", () => {
    expect(() => readDisclosure("   \n\n  \t ")).toThrow(/is empty/);
  });

  it("passes a real notice through unchanged", () => {
    expect(readDisclosure("hello")).toBe("hello");
  });
});

describe("the Disclosure component", () => {
  it("renders the text verbatim", () => {
    render(<Disclosure />);
    expect(screen.getByTestId("disclosure-text").textContent).toBe(ON_DISK);
  });

  it("marks the rendered node as verbatim", () => {
    render(<Disclosure />);
    expect(screen.getByTestId("disclosure-text")).toHaveAttribute(
      "data-disclosure-verbatim",
      "true",
    );
  });

  it("names the file it was rendered from", () => {
    render(<Disclosure />);
    expect(screen.getByTestId("disclosure").textContent).toContain("disclosure.txt");
  });
});

describe("adjacency to every performance panel", () => {
  it("the hero performance panel carries the disclosure inside itself", () => {
    render(<PerformancePanel metrics={METRICS_COMPLETE} />);
    const panel = screen.getByTestId("performance-panel");
    const disclosure = within(panel).getByTestId("disclosure-text");
    expect(disclosure).toBeInTheDocument();
    expect(disclosure.textContent).toBe(ON_DISK);
  });

  it("the expectancy island carries the disclosure inside itself", () => {
    render(<ExpectancyIsland metrics={METRICS_COMPLETE} />);
    const island = screen.getByTestId("expectancy-island");
    const disclosure = within(island).getByTestId("disclosure-text");
    expect(disclosure).toBeInTheDocument();
    expect(disclosure.textContent).toBe(ON_DISK);
  });

  it("the hero panel still carries it when the sample is too small", () => {
    render(<PerformancePanel metrics={METRICS_INSUFFICIENT} />);
    expect(
      within(screen.getByTestId("performance-panel")).getByTestId("disclosure-text")
        .textContent,
    ).toBe(ON_DISK);
  });

  it("the island still carries it when the sample is too small", () => {
    render(<ExpectancyIsland metrics={METRICS_INSUFFICIENT} />);
    expect(
      within(screen.getByTestId("expectancy-island")).getByTestId("disclosure-text")
        .textContent,
    ).toBe(ON_DISK);
  });

  it("both panels carry it when metrics have not been published at all", () => {
    render(
      <>
        <PerformancePanel metrics={null} />
        <ExpectancyIsland metrics={null} />
      </>,
    );
    // Two notices, one per panel, even with no numbers to attach them to.
    expect(screen.getAllByTestId("disclosure-text")).toHaveLength(2);
    for (const node of screen.getAllByTestId("disclosure-text")) {
      expect(node.textContent).toBe(ON_DISK);
    }
  });
});

describe("the assembled dashboard", () => {
  it("renders the disclosure inside both performance panels", () => {
    renderAppWith(fullSnapshot());

    const panels = screen.getAllByTestId("disclosure-text");
    expect(panels.length).toBeGreaterThanOrEqual(2);
    for (const node of panels) {
      expect(node.textContent).toBe(ON_DISK);
    }
  });
});
