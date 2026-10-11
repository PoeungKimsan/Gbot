/**
 * The trade tape: every closed trade, one row each, nothing interpolated.
 *
 * The five columns the tape exists for are Entry, Stop Loss, Take Profit, Slippage and
 * Realized R. Entry and Realized R are published by `state.json`'s sibling `trades.json`
 * today. Stop Loss, Take Profit and Slippage are journalled — `order_submitted` carries the
 * stop and the target, `fill_reported` and `slippage_realized` carry the slippage — but
 * `_close_trade` in `engine/publisher/outbox.py` reads one `position_closed` row and emits
 * one snapshot row, so the three live on different journal rows and never reach the
 * snapshot. `docs/PHASE_5X_SPEC.md` §2 is the ticket.
 *
 * Until then this table renders `NOT PUBLISHED BY THIS ENGINE BUILD` in those three cells,
 * for every row, and does not guess. A stop-loss of zero is a real number that would read
 * as a broker's answer; a missing stop-loss is a different fact entirely.
 */

import type { ReactElement, ReactNode } from "react";
import { useNow } from "../hooks/useNow";
import { CACHE_MAX_AGE_SECONDS, type TradesPayload, type TradeRecord } from "../api/contract";
import { ago, currency, instant, money, price, rMultiple } from "../lib/format";
import { NotPublished, OrNotPublished } from "./NotPublished";
import { Panel, Pill, cx } from "./Panel";
import { ArrowDownRight, ArrowUpRight, Table2 } from "lucide-react";

export interface TradeTapeProps {
  readonly trades: TradesPayload | null;
  /** The instrument's tick size, without which a tick count is not a price. */
  readonly tickSize: string | null;
  /** Injected in tests; defaults to the component's own one-second clock. */
  readonly now?: Date;
  readonly className?: string;
}

/** Newest first: the tape is read top-down and the oldest trade is the least relevant. */
function ordered(trades: readonly TradeRecord[]): readonly TradeRecord[] {
  return [...trades].sort((left, right) => {
    const a = left.filledAt === null ? 0 : Date.parse(left.filledAt);
    const b = right.filledAt === null ? 0 : Date.parse(right.filledAt);
    return b - a;
  });
}

export function TradeTape({ trades, tickSize, now, className }: TradeTapeProps): ReactElement {
  const clock = useNow(1_000);
  const reference = now ?? clock;
  const rows = trades === null ? [] : ordered(trades.trades);
  const missingStop = rows.filter((row) => row.stopLoss === null).length;
  const missingSlippage = rows.filter((row) => row.slippage === null).length;

  return (
    <Panel
      kicker="Completed executions"
      title="Trade tape"
      icon={<Table2 aria-hidden strokeWidth={1.25} className="h-4 w-4" />}
      className={className}
      testId="trade-tape"
      aside={
        <Pill tone={rows.length === 0 ? "warning" : "neutral"}>
          {rows.length === 0 ? "no trades" : `${rows.length} closed`}
        </Pill>
      }
      footer={
        <span className="font-display text-[0.62rem] uppercase tracking-[0.18em] text-white/25">
          {trades === null
            ? "trades.json is not published yet"
            : `${rows.length} of ${trades.count} closed trades · API cached for ${CACHE_MAX_AGE_SECONDS}s`}
        </span>
      }
    >
      {rows.length === 0 ? (
        trades === null ? (
          <NotPublished spec="§1" field="trades.json" testId="tape-empty" />
        ) : (
          <p className="py-8 text-center text-sm text-white/40">
            No completed trades journaled in this run.
          </p>
        )
      ) : (
        <div className="-mx-2 overflow-x-auto">
          <table className="w-full min-w-[52rem] border-separate border-spacing-y-1.5 px-2">
            <caption className="sr-only">
              Closed trades with entry, stop loss, take profit, slippage and realized R
            </caption>
            <thead>
              <tr className="text-left">
                <Th>Trade</Th>
                <Th>Entry</Th>
                <Th>Stop loss</Th>
                <Th>Take profit</Th>
                <Th>Slippage</Th>
                <Th>Realized R</Th>
                <Th>Exit</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.tradeId}
                  data-testid="trade-row"
                  data-trade-id={row.tradeId}
                  className="group/row align-middle"
                >
                  <td className="rounded-l-[1rem] bg-white/[0.025] px-3 py-2.5 ring-1 ring-white/[0.05]">
                    <p className="font-display text-[0.78rem] font-medium text-white/85">
                      {row.tradeId}
                    </p>
                    <p className="text-[0.62rem] text-white/35">
                      {row.window ?? "--"} · {ago(ageOf(row.filledAt, reference)) ?? "--"}
                    </p>
                  </td>
                  <Td>
                    <OrNotPublished
                      value={money(row.entryPrice)}
                      field="entry price (trades.cost)"
                      spec="§2"
                      testId={`entry-${row.tradeId}`}
                    />
                  </Td>
                  <Td>
                    <OrNotPublished
                      value={price(row.stopLoss, tickSize)}
                      field="stop_loss"
                      spec="§2"
                      testId={`stop-${row.tradeId}`}
                    />
                  </Td>
                  <Td>
                    <OrNotPublished
                      value={price(row.takeProfit, tickSize)}
                      field="take_profit"
                      spec="§2"
                      testId={`target-${row.tradeId}`}
                    />
                  </Td>
                  <Td>
                    <OrNotPublished
                      value={row.slippage === null ? null : currency(row.slippage)}
                      field="slippage"
                      spec="§2"
                      testId={`slippage-${row.tradeId}`}
                    />
                  </Td>
                  <Td>
                    <span
                      data-testid={`r-${row.tradeId}`}
                      className={cx(
                        "font-display text-[0.8rem] font-semibold tabular-nums",
                        Number(row.realizedR) > 0
                          ? "text-emerald-300"
                          : Number(row.realizedR) < 0
                            ? "text-rose-300"
                            : "text-white/60",
                      )}
                    >
                      {rMultiple(row.realizedR) ?? "--"}
                    </span>
                  </Td>
                  <Td>
                    <span className="flex flex-col items-start gap-1">
                      <Pill tone={Number(row.realizedR) > 0 ? "positive" : "negative"}>
                        {Number(row.realizedR) > 0 ? (
                          <ArrowUpRight aria-hidden strokeWidth={1.25} className="h-3 w-3" />
                        ) : (
                          <ArrowDownRight aria-hidden strokeWidth={1.25} className="h-3 w-3" />
                        )}
                        {row.exitReason}
                      </Pill>
                      <span className="font-display text-[0.68rem] tabular-nums text-white/40">
                        {money(row.exitPrice) === null ? (
                          <NotPublished field="exit price (trades.price)" spec="§2" compact />
                        ) : (
                          money(row.exitPrice)
                        )}
                      </span>
                    </span>
                  </Td>
                  <td className="rounded-r-[1rem] bg-white/[0.015] px-3 py-2.5 text-right text-[0.66rem] text-white/30 ring-1 ring-white/[0.05]">
                    {instant(row.filledAt, reference) ?? "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {(missingStop > 0 || missingSlippage > 0) && (
        <p
          data-testid="tape-gaps"
          className="mt-4 rounded-[1.1rem] bg-white/[0.02] p-4 text-[0.7rem] leading-relaxed text-white/45 ring-1 ring-white/[0.06]"
        >
          {missingStop} of {rows.length} rows have no published stop loss, take profit, or
          slippage. Those values exist in the journal but are not folded into{" "}
          <code className="text-white/60">trades.json</code>; see docs/PHASE_5X_SPEC.md §2.
        </p>
      )}
    </Panel>
  );
}

function Th({ children }: { readonly children: string }): ReactElement {
  return (
    <th
      scope="col"
      className="px-3 pb-1.5 font-display text-[0.58rem] font-medium uppercase tracking-[0.18em] text-white/35"
    >
      {children}
    </th>
  );
}

function Td({ children }: { readonly children: ReactNode }): ReactElement {
  return (
    <td className="bg-white/[0.015] px-3 py-2.5 font-display text-[0.8rem] tabular-nums ring-1 ring-white/[0.05]">
      {children}
    </td>
  );
}

function ageOf(iso: string | null, now: Date): number | null {
  if (iso === null) {
    return null;
  }
  const parsed = Date.parse(iso);
  return Number.isNaN(parsed) ? null : Math.max(0, (now.getTime() - parsed) / 1000);
}
