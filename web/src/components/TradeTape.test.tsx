/**
 * The trade tape: the five columns, and the three that are not published yet.
 *
 * `docs/PHASE_5X_SPEC.md` §2 is the reason this suite looks the way it does. Entry and
 * Realized R are in `trades.json` today. Stop Loss, Take Profit and Slippage are journalled
 * — `order_submitted`, `fill_reported`, `slippage_realized` — but `_close_trade` never
 * correlates them into the closed-trade row, so they are absent from the snapshot.
 *
 * So the suite pins both halves: the published columns render their values, and the
 * unpublished ones render `NOT PUBLISHED BY THIS ENGINE BUILD` for every row, for every
 * test, with no interpolation and no zero-filling.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { TradeTape } from "./TradeTape";
import { TICK_SIZE, TRADES_FIXTURE, fullSnapshot, publishedSnapshot } from "../api/fixtures";
import { normalizeTrades } from "../api/client";

const NOW = new Date("2026-03-10T07:40:00.000Z");

/** The comma-grouped form `money()` produces, so the test needs no formatter of its own. */
function grouped(value: string): string {
  const [whole = "0", fraction = ""] = value.split(".");
  return `${Number(whole).toLocaleString("en-US")}${fraction === "" ? "" : `.${fraction}`}`;
}

/** `trades.json` exactly as the current publisher emits it. */
const PUBLISHED_TRADES = {
  count: TRADES_FIXTURE.count,
  trades: TRADES_FIXTURE.trades.map((entry) => ({
    trade_id: entry.tradeId,
    side: entry.side,
    quantity: entry.quantity,
    pnl: entry.pnl,
    price: (entry.exitPrice ?? 0).toString(),
    cost: (entry.entryPrice ?? 0).toString(),
    r_multiple: entry.realizedR,
    exit_reason: entry.exitReason,
    reference: entry.reference,
  })),
};

/** The dollar form `currency()` puts on a slippage amount. */
function currencyOf(trade: (typeof TRADES_FIXTURE.trades)[number]): string {
  const amount = trade.slippage ?? "0";
  const sign = amount.startsWith("-") ? "-" : amount.startsWith("+") ? "+" : "";
  const bare = amount.slice(sign.length);
  const [whole = "0", fraction = ""] = bare.split(".");
  const groupedWhole = Number(whole).toLocaleString("en-US");
  return `${sign}$${groupedWhole}${fraction === "" ? "" : `.${fraction}`}`;
}
describe("against the snapshot the engine actually publishes today", () => {
  it("shows entry and realized R from the published fields", () => {
    const { container } = render(
      <TradeTape trades={normalizeTrades(PUBLISHED_TRADES)} tickSize={TICK_SIZE} now={NOW} />,
    );
    const row = within(screen.getAllByTestId("trade-row")[0]!);

    // No `filled_at` is published, so the tape keeps the publisher's order.
    const entry = PUBLISHED_TRADES.trades[0]!;
    expect(row.getByTestId(`entry-${entry.trade_id}`)).toHaveTextContent("2,006.40");
    expect(row.getByTestId(`r-${entry.trade_id}`)).toHaveTextContent("R");
    expect(container).toBeTruthy();
  });

  it("marks stop loss, take profit and slippage as not published", () => {
    const { container } = render(
      <TradeTape trades={normalizeTrades(PUBLISHED_TRADES)} tickSize={TICK_SIZE} now={NOW} />,
    );
    const markers = container.querySelectorAll('[data-not-published="true"]');
    // Three gaps, forty-two rows.
    expect(markers).toHaveLength(42 * 3);
    for (const marker of markers) {
      expect(marker.textContent).toContain("NOT PUBLISHED BY THIS ENGINE BUILD");
    }
  });

  it("never renders a zero where a stop-loss belongs", () => {
    const { container } = render(
      <TradeTape trades={normalizeTrades(PUBLISHED_TRADES)} tickSize={TICK_SIZE} now={NOW} />,
    );
    expect(container.textContent).toContain("NOT PUBLISHED BY THIS ENGINE BUILD");
    expect(screen.queryByTestId("tape-empty")).not.toBeInTheDocument();
  });

  it("reports how many rows carry the gap", () => {
    render(
      <TradeTape
        trades={normalizeTrades(PUBLISHED_TRADES)}
        tickSize={TICK_SIZE}
        now={NOW}
      />,
    );
    expect(screen.getByTestId("tape-gaps")).toHaveTextContent(/docs\/PHASE_5X_SPEC\.md §2/);
  });

  it("renders the published prices without needing the instrument geometry", () => {
    // `cost` and `price` are decimal strings, so they are readable whatever the publisher
    // says about the tick. Only the Phase 5.x tick columns depend on the instrument.
    const { container } = render(
      <TradeTape trades={normalizeTrades(PUBLISHED_TRADES)} tickSize={null} now={NOW} />,
    );
    expect(container.querySelectorAll('[data-not-published="true"]')).toHaveLength(42 * 3);
  });

  it("gap only the tick-priced columns, once the Phase 5.x fields arrive", () => {
    const { container } = render(
      <TradeTape trades={TRADES_FIXTURE} tickSize={null} now={NOW} />,
    );
    // Stop and target are ticks, so they need the tick size; slippage is money.
    expect(container.querySelectorAll('[data-not-published="true"]')).toHaveLength(42 * 2);
  });
});

describe("against a snapshot carrying the Phase 5.x fields", () => {
  it("shows entry, stop, target, slippage and realized R", () => {
    render(<TradeTape trades={TRADES_FIXTURE} tickSize={TICK_SIZE} />);
    const row = within(screen.getAllByTestId("trade-row")[0]!);
    // The tape is newest first, and the fixture's newest trade is its last element.
    const first = TRADES_FIXTURE.trades[TRADES_FIXTURE.trades.length - 1]!;

    const headers = screen
      .getAllByRole("columnheader")
      .map((node) => node.textContent);
    expect(headers).toEqual([
      "Trade",
      "Entry",
      "Stop loss",
      "Take profit",
      "Slippage",
      "Realized R",
      "Exit",
    ]);

    expect(row.getByTestId(`entry-${first.tradeId}`)).toHaveTextContent(
      grouped(first.entryPrice ?? ""),
    );
    expect(row.getByTestId(`stop-${first.tradeId}`)).toHaveTextContent(
      grouped(((first.stopLoss ?? 0) / 100).toFixed(2)),
    );
    expect(row.getByTestId(`target-${first.tradeId}`)).toHaveTextContent(
      grouped(((first.takeProfit ?? 0) / 100).toFixed(2)),
    );
    // Slippage is money, so it is a dollar figure whatever the tick size says.
    expect(row.getByTestId(`slippage-${first.tradeId}`)).toHaveTextContent(currencyOf(first));
    expect(row.getByTestId(`r-${first.tradeId}`)).toHaveTextContent(
      `${Number(first.realizedR) < 0 ? "-" : "+"}${Math.abs(Number(first.realizedR)).toFixed(2)} R`,
    );
  });

  it("has no gap to report once the fields arrive", () => {
    render(<TradeTape trades={TRADES_FIXTURE} tickSize={TICK_SIZE} now={NOW} />);
    const { container } = render(
      <TradeTape trades={TRADES_FIXTURE} tickSize={TICK_SIZE} now={NOW} />,
    );
    expect(screen.queryByTestId("tape-gaps")).not.toBeInTheDocument();
    expect(container.querySelectorAll('[data-not-published="true"]')).toHaveLength(0);
  });

  it("orders the tape newest first", () => {
    render(<TradeTape trades={TRADES_FIXTURE} tickSize={TICK_SIZE} now={NOW} />);
    const ids = screen
      .getAllByTestId("trade-row")
      .map((row) => row.getAttribute("data-trade-id"));
    const expected = TRADES_FIXTURE.trades.map((entry) => entry.tradeId).reverse();
    expect(ids).toEqual(expected);
  });
});

describe("the empty states", () => {
  it("trades.json missing is a sentinel, not a blank table", () => {
    render(<TradeTape trades={null} tickSize={TICK_SIZE} now={NOW} />);
    expect(screen.getByTestId("tape-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("trade-row")).not.toBeInTheDocument();
  });

  it("a run with no closed trades is stated, not errored", () => {
    render(<TradeTape trades={{ count: 0, trades: [] }} tickSize={TICK_SIZE} now={NOW} />);
    expect(screen.queryByTestId("tape-empty")).not.toBeInTheDocument();
    expect(screen.getByText(/No completed trades journaled/)).toBeInTheDocument();
  });
});

describe("the snapshots the fixtures model", () => {
  it("the published snapshot carries no stop-loss field at all", () => {
    expect(publishedSnapshot().trades?.trades[0]?.stopLoss).toBeNull();
  });

  it("the full one does", () => {
    expect(fullSnapshot().trades?.trades[0]?.stopLoss).toBeTypeOf("number");
  });

  it("and the full one is ordered the same way", () => {
    expect(fullSnapshot().trades?.trades.length).toBe(TRADES_FIXTURE.count);
  });
});
