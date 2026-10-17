/**
 * 12-ETF Strategy Radar — Supabase Technical Minimalist Design.
 *
 * Displays ranking across the 12 universe assets:
 * 9-Month Momentum, 12-Month Volatility, Cash Filter hurdle status,
 * Systematic Target Weights, and an Admin PIN-protected rebalance execution modal.
 */

import { useState, type ReactElement } from "react";
import { Compass, Key, Lock, RefreshCw, ShieldAlert, X } from "lucide-react";
import type { ETFScore } from "../hooks/useLiveTrader";

interface ETFRadarProps {
  readonly scores: ETFScore[];
  readonly onRebalance?: (pin?: string, force?: boolean) => Promise<{ success: boolean; message: string }>;
  readonly isRebalancing?: boolean;
  readonly className?: string;
}

export function ETFRadar({
  scores,
  onRebalance,
  isRebalancing = false,
  className = "",
}: ETFRadarProps): ReactElement {
  const [showModal, setShowModal] = useState(false);
  const [pin, setPin] = useState(() => localStorage.getItem("gbot_admin_pin") || "");
  const [remember, setRemember] = useState(true);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const handleOpenClick = () => {
    setFeedback(null);
    const saved = localStorage.getItem("gbot_admin_pin");
    if (saved && onRebalance) {
      // Try with saved PIN directly
      void executeWithPin(saved);
    } else {
      setShowModal(true);
    }
  };

  const executeWithPin = async (pinToUse: string, force = false) => {
    if (!onRebalance) return;
    const res = await onRebalance(pinToUse, force);
    if (res.success) {
      if (remember) {
        localStorage.setItem("gbot_admin_pin", pinToUse);
      }
      setFeedback({ type: "success", text: res.message });
      setShowModal(false);
    } else {
      setFeedback({ type: "error", text: res.message });
      // If unauthorized, show modal so user can correct PIN
      setShowModal(true);
    }
  };

  return (
    <div
      className={`rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-sm flex flex-col justify-between gap-4 relative ${className}`}
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-[#282828] pb-4">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-md bg-[#242424] border border-[#333333] text-[#ededed]">
            <Compass className="h-4 w-4" strokeWidth={1.5} />
          </div>
          <div>
            <h3 className="font-sans text-sm font-medium text-[#ededed]">VAR_117 Strategy Radar</h3>
            <p className="text-xs text-[#707070]">12-ETF Momentum, Volatility & Cash Filter</p>
          </div>
        </div>

        {onRebalance && (
          <div className="flex items-center gap-2">
            <button
              onClick={handleOpenClick}
              disabled={isRebalancing}
              className="flex items-center gap-2 rounded bg-[#202020] hover:bg-[#282828] border border-[#333333] hover:border-[#3ecf8e] px-3 py-1.5 font-mono text-xs text-[#ededed] transition-colors disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${isRebalancing ? "animate-spin text-[#3ecf8e]" : "text-[#9a9a9a]"}`}
              />
              <span>{isRebalancing ? "Rebalancing..." : "Trigger Rebalance"}</span>
            </button>

            <button
              onClick={() => {
                setFeedback(null);
                setShowModal(true);
              }}
              title="Admin PIN Settings"
              className="flex h-7 w-7 items-center justify-center rounded bg-[#202020] hover:bg-[#282828] border border-[#333333] text-[#707070] hover:text-[#ededed] transition-colors"
            >
              <Key className="h-3.5 w-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* Feedback Toast Banner */}
      {feedback && (
        <div
          className={`flex items-center justify-between rounded px-3 py-2 text-xs font-mono border ${
            feedback.type === "success"
              ? "bg-[#162920] border-[#1f4a33] text-[#3ecf8e]"
              : "bg-[#331118] border-[#591622] text-[#f43f5e]"
          }`}
        >
          <span>{feedback.text}</span>
          <button onClick={() => setFeedback(null)} className="text-[#9a9a9a] hover:text-[#ededed]">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Radar Table */}
      <div className="overflow-x-auto flex-1">
        <table className="w-full text-left font-sans text-xs">
          <thead>
            <tr className="border-b border-[#282828] text-[#707070]">
              <th className="pb-2.5 font-medium w-10 text-center">#</th>
              <th className="pb-2.5 font-medium">Asset</th>
              <th className="pb-2.5 font-medium text-right">9M Return</th>
              <th className="pb-2.5 font-medium text-right">12M Vol</th>
              <th className="pb-2.5 font-medium text-center">Cash Filter</th>
              <th className="pb-2.5 font-medium text-right">Target %</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#242424] font-mono text-xs">
            {scores.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-6 text-center text-[#707070] font-sans">
                  Awaiting strategy ranking scores from LiveTrader...
                </td>
              </tr>
            ) : (
              scores.map((s) => {
                const isSelected = s.rank !== null && s.rank <= 3;
                const mom = parseFloat(s.mom_9m_pct);
                const targetWeightPct = (parseFloat(s.target_weight) * 100).toFixed(1);

                return (
                  <tr
                    key={s.ticker}
                    className={`transition-colors ${
                      isSelected ? "bg-[#1f2824]/40 hover:bg-[#1f2824]/70" : "hover:bg-[#202020]"
                    }`}
                  >
                    <td className="py-2.5 text-center">
                      {s.rank !== null ? (
                        <span
                          className={`inline-flex h-5 w-5 items-center justify-center rounded text-[11px] font-bold ${
                            s.rank <= 3
                              ? "bg-[#162920] border border-[#1f4a33] text-[#3ecf8e]"
                              : "bg-[#242424] text-[#707070]"
                          }`}
                        >
                          {s.rank}
                        </span>
                      ) : (
                        <span className="text-[#555555]">-</span>
                      )}
                    </td>
                    <td className="py-2.5">
                      <div className="font-semibold text-[#ededed] flex items-center gap-1.5">
                        {s.ticker}
                        {isSelected && (
                          <span className="text-[10px] uppercase font-bold text-[#3ecf8e] bg-[#162920] px-1 py-0.2 rounded border border-[#1f4a33]">
                            Top 3
                          </span>
                        )}
                      </div>
                      <div className="font-sans text-[11px] text-[#707070]">{s.category}</div>
                    </td>
                    <td
                      className={`py-2.5 text-right font-medium ${
                        mom > 0 ? "text-[#3ecf8e]" : "text-[#f43f5e]"
                      }`}
                    >
                      {mom > 0 ? `+${s.mom_9m_pct}%` : `${s.mom_9m_pct}%`}
                    </td>
                    <td className="py-2.5 text-right text-[#9a9a9a]">{s.vol_12m_pct}%</td>
                    <td className="py-2.5 text-center">
                      {s.cash_filter_status === "PASS" ? (
                        <span className="inline-block rounded bg-[#162920] border border-[#1f4a33] px-1.5 py-0.5 text-[10px] font-semibold text-[#3ecf8e]">
                          PASS
                        </span>
                      ) : s.cash_filter_status === "FAIL" ? (
                        <span className="inline-block rounded bg-[#331118] border border-[#591622] px-1.5 py-0.5 text-[10px] font-semibold text-[#f43f5e]">
                          FAIL (CASH)
                        </span>
                      ) : (
                        <span className="text-[10px] text-[#555555]">N/A</span>
                      )}
                    </td>
                    <td className="py-2.5 text-right">
                      {parseFloat(s.target_weight) > 0 ? (
                        <span className="font-bold text-[#3ecf8e]">{targetWeightPct}%</span>
                      ) : (
                        <span className="text-[#555555]">0.0%</span>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Footer Universe Status Bar */}
      <div className="mt-auto pt-3 border-t border-[#282828] flex items-center justify-between text-[11px] font-mono text-[#707070]">
        <div className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-[#3ecf8e]" />
          <span>VAR_117 Multi-Asset Universe (12 ETFs)</span>
        </div>
        <span className="hidden sm:inline">Lookback: 9M Mom • 12M Vol</span>
      </div>

      {/* Admin Authorization Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4 backdrop-blur-sm animate-in fade-in">
          <div className="w-full max-w-sm rounded-lg border border-[#282828] bg-[#1c1c1c] p-6 shadow-2xl flex flex-col gap-4">
            <div className="flex items-center justify-between border-b border-[#282828] pb-3">
              <div className="flex items-center gap-2">
                <div className="flex h-7 w-7 items-center justify-center rounded bg-[#242424] border border-[#333333] text-[#3ecf8e]">
                  <Lock className="h-4 w-4" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-[#ededed]">Admin Authorization</h4>
                  <p className="text-[11px] text-[#707070]">Protected execution control</p>
                </div>
              </div>
              <button
                onClick={() => setShowModal(false)}
                className="text-[#707070] hover:text-[#ededed]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {feedback?.type === "error" && (
              <div className="rounded bg-[#331118] border border-[#591622] p-2.5 text-xs font-mono text-[#f43f5e] flex items-center gap-2">
                <ShieldAlert className="h-4 w-4 shrink-0" />
                <span>{feedback.text}</span>
              </div>
            )}

            <div>
              <label className="block text-xs font-medium text-[#707070] mb-1 font-sans">
                Admin Passcode / PIN
              </label>
              <input
                type="password"
                value={pin}
                onChange={(e) => setPin(e.target.value)}
                placeholder="••••"
                autoFocus
                className="w-full rounded bg-[#171717] border border-[#333333] focus:border-[#3ecf8e] px-3 py-2 font-mono text-sm text-[#ededed] outline-none"
              />
            </div>

            <label className="flex items-center gap-2 text-xs text-[#9a9a9a] cursor-pointer">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="rounded border-[#333333] bg-[#242424] accent-[#3ecf8e]"
              />
              <span>Remember PIN on this browser</span>
            </label>

            <div className="flex items-center justify-end gap-2 pt-2 border-t border-[#282828]">
              <button
                onClick={() => setShowModal(false)}
                className="rounded bg-[#202020] hover:bg-[#282828] border border-[#333333] px-3 py-1.5 text-xs text-[#9a9a9a] hover:text-[#ededed]"
              >
                Cancel
              </button>
              <button
                onClick={() => executeWithPin(pin)}
                disabled={!pin || isRebalancing}
                className="rounded bg-[#3ecf8e] hover:bg-[#34b27b] px-4 py-1.5 text-xs font-semibold text-[#121212] transition-colors disabled:opacity-50"
              >
                {isRebalancing ? "Executing..." : "Confirm & Execute"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
