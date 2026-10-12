/**
 * Real-time WebSocket hook connecting the dashboard to the VAR_117 LiveTrader stream.
 *
 * Provides real-time portfolio NAV, active ETF holdings with weight drift,
 * 12-ETF momentum radar, rebalance execution signals, and kill-switch status.
 */

import { useEffect, useRef, useState } from "react";

export interface LiveBar {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface LiveTick {
  bid: string;
  ask: string;
  mid: string;
  spread_ticks: number;
  time: number;
}

export interface LivePosition {
  ticket: number;
  time: string;
  side: "BUY" | "SELL";
  volume: string;
  open_price: string;
  current_price: string;
  sl: string | null;
  tp: string | null;
  profit: string;
  comment: string;
}

export interface LiveTrade {
  id: number;
  side: "BUY" | "SELL";
  volume: string;
  entry: string;
  sl: string | null;
  tp: string | null;
  time: string;
  status: string;
  strategy: string;
}

export interface AsianRange {
  date: string;
  high: string;
  low: string;
  complete: boolean;
}

export interface ETFScore {
  ticker: string;
  name: string;
  category: string;
  close_price: string;
  mom_9m_pct: string;
  vol_12m_pct: string;
  rank: number | null;
  cash_filter_status: string; // "PASS" | "FAIL" | "N/A"
  target_weight: string;
}

export interface Holding {
  symbol: string;
  name: string;
  category: string;
  qty: string;
  avg_entry_price: string;
  current_price: string;
  market_value: string;
  current_weight_pct: string;
  target_weight_pct: string;
  drift_pct: string;
  unrealized_pl: string;
}

export interface RebalanceInfo {
  last_rebalance_time: string | null;
  next_rebalance_date: string;
  cash_weight_pct: string;
  top_assets: string[];
  signal_hash: string | null;
}

export interface KillSwitchInfo {
  state: string;
  is_tripped: boolean;
  loss_r: string;
  limit_r: string;
  trip_reason: string | null;
}

export interface RebalanceOrder {
  id: string;
  symbol: string;
  qty: string;
  side: string;
  type: string;
  status: string;
  filled_avg_price?: string;
  created_at: string;
  mode?: string;
}

export interface LiveSnapshot {
  strategy: string;
  broker_mode: string;
  account_equity: string;
  account_balance?: string;
  cash_balance: string;
  invested_value: string;
  unrealized_pnl: string;
  realized_pnl_today: string;
  drawdown_pct: string;
  high_water_mark: string;
  killswitch: KillSwitchInfo;
  holdings: Holding[];
  radar: ETFScore[];
  rebalance_info: RebalanceInfo;
  recent_orders: RebalanceOrder[];
  equity_history: { time: number; equity: number; cash: number }[];
  updated_at: string;

  // Optional legacy fields for backwards compatibility with older components
  symbol?: string;
  bid?: string;
  ask?: string;
  mid?: string;
  spread_ticks?: string;
  daily_change_pct?: string;
  daily_high?: string;
  daily_low?: string;
  open_positions?: any[];
  completed_trades?: any[];
  recent_bars?: LiveBar[];
  asian_range?: AsianRange | null;
  latest_signal?: Record<string, unknown> | null;
}

export function useLiveTrader() {
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null);
  const [latestTick, setLatestTick] = useState<LiveTick | null>(null);
  const [newBar, setNewBar] = useState<LiveBar | null>(null);
  const [connected, setConnected] = useState(false);
  const [priceFlash, setPriceFlash] = useState<"up" | "down" | null>(null);
  const [isRebalancing, setIsRebalancing] = useState(false);

  const prevMid = useRef<number | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const triggerRebalance = async (
    pin?: string,
    force?: boolean
  ): Promise<{ success: boolean; message: string }> => {
    setIsRebalancing(true);
    try {
      const isHttps = window.location.protocol === "https:";
      const proto = isHttps ? "https:" : "http:";
      const host = window.location.hostname || "127.0.0.1";
      const port =
        window.location.port && window.location.port !== "8080" ? "8080" : window.location.port;
      const url = `${proto}//${host}${port ? `:${port}` : ""}/api/rebalance/execute${
        force ? "?force=true" : ""
      }`;

      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(pin ? { "X-Admin-Pin": pin } : {}),
        },
      });

      const body = await res.json().catch(() => ({}));

      if (res.ok) {
        const statusRes = await fetch(
          `${proto}//${host}${port ? `:${port}` : ""}/api/status`
        );
        if (statusRes.ok) {
          const snap = await statusRes.json();
          setSnapshot(snap);
        }
        return { success: true, message: "Rebalance executed successfully!" };
      } else {
        return {
          success: false,
          message: body.message || `Request failed (${res.status})`,
        };
      }
    } catch (e: any) {
      console.error("Failed to trigger rebalance:", e);
      return { success: false, message: e.message || "Network error" };
    } finally {
      setIsRebalancing(false);
    }
  };

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    const connect = () => {
      const isHttps = window.location.protocol === "https:";
      const wsProto = isHttps ? "wss:" : "ws:";
      const host = window.location.hostname || "127.0.0.1";

      let wsUrl: string;
      if (window.location.port && window.location.port !== "8080") {
        wsUrl = `${wsProto}//${host}:8080/ws/live`;
      } else if (window.location.port === "8080") {
        wsUrl = `${wsProto}//${host}:8080/ws/live`;
      } else {
        wsUrl = `${wsProto}//${host}/ws/live`;
      }

      try {
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
          if (!isMounted) return;
          setConnected(true);
        };

        ws.onmessage = (event) => {
          if (!isMounted) return;
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === "init") {
              const snap = msg.data as LiveSnapshot;
              setSnapshot(snap);
            } else if (msg.type === "portfolio_update") {
              const data = msg.data;
              setSnapshot((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  account_equity: data.equity,
                  cash_balance: data.cash,
                  invested_value: data.invested,
                  drawdown_pct: data.drawdown_pct,
                };
              });
            } else if (msg.type === "rebalance_executed") {
              const data = msg.data;
              setSnapshot((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  recent_orders: [...(prev.recent_orders || []), ...(data.orders || [])],
                };
              });
            } else if (msg.type === "killswitch_tripped") {
              const data = msg.data;
              setSnapshot((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  killswitch: {
                    ...prev.killswitch,
                    state: "TRIPPED",
                    is_tripped: true,
                    loss_r: data.loss_r || prev.killswitch.loss_r,
                    trip_reason: data.reason || prev.killswitch.trip_reason,
                  },
                };
              });
            } else if (msg.type === "tick") {
              const tick = msg.data as LiveTick;
              setLatestTick(tick);
              const currentMid = parseFloat(tick.mid);
              if (prevMid.current !== null) {
                if (currentMid > prevMid.current) {
                  setPriceFlash("up");
                } else if (currentMid < prevMid.current) {
                  setPriceFlash("down");
                }
                if (flashTimer.current) clearTimeout(flashTimer.current);
                flashTimer.current = setTimeout(() => setPriceFlash(null), 350);
              }
              prevMid.current = currentMid;
            } else if (msg.type === "bar") {
              setNewBar(msg.data as LiveBar);
            }
          } catch (e) {
            console.error("Error parsing WebSocket event:", e);
          }
        };

        ws.onclose = () => {
          if (!isMounted) return;
          setConnected(false);
          reconnectTimeout = setTimeout(connect, 2000);
        };

        ws.onerror = () => {
          if (ws) ws.close();
        };
      } catch (err) {
        console.error("WebSocket connection error:", err);
        reconnectTimeout = setTimeout(connect, 3000);
      }
    };

    connect();

    return () => {
      isMounted = false;
      if (ws) ws.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (flashTimer.current) clearTimeout(flashTimer.current);
    };
  }, []);

  return {
    connected,
    snapshot,
    latestTick,
    newBar,
    priceFlash,
    triggerRebalance,
    isRebalancing,
  };
}
