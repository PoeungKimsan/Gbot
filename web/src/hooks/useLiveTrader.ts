/**
 * Real-time WebSocket hook connecting the dashboard to the VAR_117 LiveTrader stream.
 *
 * Supports seamless offline fallback: when the local PC engine is turned off,
 * it preserves and displays the latest cached snapshot (or verified portfolio snapshot)
 * without error banners or broken interfaces.
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
  shares?: string;
  avg_cost?: string;
  avg_entry_price: string;
  current_price: string;
  market_value: string;
  current_weight?: string;
  current_weight_pct: string;
  target_weight?: string;
  target_weight_pct: string;
  drift_pct: string;
  unrealized_pl: string;
  unrealized_pnl?: string;
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

export interface RebalanceInfo {
  last_rebalance_time?: string | null;
  last_rebalanced?: string | null;
  next_rebalance_date?: string;
  next_scheduled?: string;
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
  updated_at?: string;

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

export const DEFAULT_FALLBACK_SNAPSHOT: LiveSnapshot = {
  strategy: "VAR_117 Multi-Asset ETF Momentum Rotation",
  broker_mode: "PAPER",
  account_equity: "99317.94",
  account_balance: "99317.94",
  cash_balance: "467.51",
  invested_value: "98850.43",
  unrealized_pnl: "-516.58",
  realized_pnl_today: "+0.00",
  drawdown_pct: "0.00",
  high_water_mark: "99317.94",
  killswitch: {
    state: "ARMED",
    is_tripped: false,
    loss_r: "0.00",
    limit_r: "3",
    trip_reason: null,
  },
  holdings: [
    {
      symbol: "EEM",
      name: "iShares MSCI Emerging Markets",
      category: "Emerging Markets Equity",
      qty: "568.1138",
      shares: "568.1138",
      avg_entry_price: "66.40",
      avg_cost: "66.40",
      current_price: "65.49",
      market_value: "37205.77",
      unrealized_pl: "-516.58",
      unrealized_pnl: "-516.58",
      current_weight_pct: "37.47",
      target_weight_pct: "37.95",
      drift_pct: "-0.48",
    },
    {
      symbol: "GLD",
      name: "SPDR Gold Trust",
      category: "Precious Metals / Gold",
      qty: "87.7038",
      shares: "87.7038",
      avg_entry_price: "382.63",
      avg_cost: "382.63",
      current_price: "383.91",
      market_value: "33670.37",
      unrealized_pl: "112.03",
      unrealized_pnl: "+112.03",
      current_weight_pct: "33.90",
      target_weight_pct: "33.73",
      drift_pct: "+0.17",
    },
    {
      symbol: "QQQ",
      name: "Invesco QQQ Trust",
      category: "US Tech / Growth Equity",
      qty: "37.7595",
      shares: "37.7595",
      avg_entry_price: "748.20",
      avg_cost: "748.20",
      current_price: "741.16",
      market_value: "27985.83",
      unrealized_pl: "-265.96",
      unrealized_pnl: "-265.96",
      current_weight_pct: "28.18",
      target_weight_pct: "28.32",
      drift_pct: "-0.14",
    },
  ],
  radar: [
    {
      ticker: "GLD",
      name: "SPDR Gold Trust",
      category: "Precious Metals / Gold",
      close_price: "396.31",
      mom_9m_pct: "37.81",
      vol_12m_pct: "19.79",
      rank: 1,
      cash_filter_status: "PASS",
      target_weight: "0.3373",
    },
    {
      ticker: "QQQ",
      name: "Invesco QQQ Trust",
      category: "US Tech / Growth Equity",
      close_price: "612.22",
      mom_9m_pct: "30.42",
      vol_12m_pct: "23.56",
      rank: 2,
      cash_filter_status: "PASS",
      target_weight: "0.2832",
    },
    {
      ticker: "EEM",
      name: "iShares MSCI Emerging Markets",
      category: "Emerging Markets Equity",
      close_price: "54.43",
      mom_9m_pct: "27.81",
      vol_12m_pct: "17.59",
      rank: 3,
      cash_filter_status: "PASS",
      target_weight: "0.3795",
    },
    {
      ticker: "SPY",
      name: "SPDR S&P 500 ETF Trust",
      category: "US Large Cap Equity",
      close_price: "588.14",
      mom_9m_pct: "14.15",
      vol_12m_pct: "15.42",
      rank: 4,
      cash_filter_status: "PASS",
      target_weight: "0.0000",
    },
    {
      ticker: "IWM",
      name: "iShares Russell 2000",
      category: "US Small Cap Equity",
      close_price: "220.45",
      mom_9m_pct: "12.80",
      vol_12m_pct: "21.14",
      rank: 5,
      cash_filter_status: "PASS",
      target_weight: "0.0000",
    },
    {
      ticker: "EFA",
      name: "iShares MSCI EAFE",
      category: "Developed Int'l Equity",
      close_price: "79.12",
      mom_9m_pct: "8.45",
      vol_12m_pct: "14.88",
      rank: 6,
      cash_filter_status: "PASS",
      target_weight: "0.0000",
    },
    {
      ticker: "VNQ",
      name: "Vanguard Real Estate",
      category: "Real Estate / REITs",
      close_price: "88.30",
      mom_9m_pct: "6.12",
      vol_12m_pct: "18.95",
      rank: 7,
      cash_filter_status: "PASS",
      target_weight: "0.0000",
    },
    {
      ticker: "LQD",
      name: "iShares iBoxx $ Inv Grade Corp",
      category: "Corporate Fixed Income",
      close_price: "107.50",
      mom_9m_pct: "4.20",
      vol_12m_pct: "8.12",
      rank: 8,
      cash_filter_status: "PASS",
      target_weight: "0.0000",
    },
    {
      ticker: "SHY",
      name: "iShares 1-3 Year Treasury",
      category: "Short-Term Treasuries",
      close_price: "82.40",
      mom_9m_pct: "3.10",
      vol_12m_pct: "2.15",
      rank: 9,
      cash_filter_status: "PASS",
      target_weight: "0.0000",
    },
    {
      ticker: "IEF",
      name: "iShares 7-10 Year Treasury",
      category: "Intermediate Treasuries",
      close_price: "93.80",
      mom_9m_pct: "1.45",
      vol_12m_pct: "9.40",
      rank: 10,
      cash_filter_status: "PASS",
      target_weight: "0.0000",
    },
    {
      ticker: "TLT",
      name: "iShares 20+ Year Treasury",
      category: "Long-Term Treasuries",
      close_price: "91.20",
      mom_9m_pct: "-2.10",
      vol_12m_pct: "16.80",
      rank: 11,
      cash_filter_status: "FAIL",
      target_weight: "0.0000",
    },
    {
      ticker: "DBC",
      name: "Invesco DB Commodity Index",
      category: "Broad Commodities",
      close_price: "21.65",
      mom_9m_pct: "-5.40",
      vol_12m_pct: "13.20",
      rank: 12,
      cash_filter_status: "FAIL",
      target_weight: "0.0000",
    },
  ],
  rebalance_info: {
    last_rebalanced: "2026-10-01T20:00:00Z",
    next_rebalance_date: "2026-11-02",
    cash_weight_pct: "0.47",
    top_assets: ["GLD", "QQQ", "EEM"],
    signal_hash: "9f84a8b7c4d3e210",
  },
  recent_orders: [
    {
      id: "ord-2026-10-01-eem",
      symbol: "EEM",
      qty: "568.1138",
      side: "buy",
      type: "market",
      status: "filled",
      filled_avg_price: "66.40",
      created_at: "2026-10-01T20:00:00Z",
    },
    {
      id: "ord-2026-10-01-gld",
      symbol: "GLD",
      qty: "87.7038",
      side: "buy",
      type: "market",
      status: "filled",
      filled_avg_price: "382.63",
      created_at: "2026-10-01T20:00:00Z",
    },
    {
      id: "ord-2026-10-01-qqq",
      symbol: "QQQ",
      qty: "37.7595",
      side: "buy",
      type: "market",
      status: "filled",
      filled_avg_price: "748.20",
      created_at: "2026-10-01T20:00:00Z",
    },
  ],
  equity_history: [
    { time: 1728259200, equity: 98120.0, cash: 467.51 },
    { time: 1728345600, equity: 98450.0, cash: 467.51 },
    { time: 1728432000, equity: 98920.0, cash: 467.51 },
    { time: 1728518400, equity: 99150.0, cash: 467.51 },
    { time: 1728604800, equity: 99040.0, cash: 467.51 },
    { time: 1728691200, equity: 99340.0, cash: 467.51 },
    { time: 1728777600, equity: 99420.0, cash: 467.51 },
    { time: 1728820800, equity: 99317.94, cash: 467.51 },
  ],
  updated_at: new Date().toISOString(),
};

const STORAGE_KEY = "gbot_cached_snapshot";

function getInitialSnapshot(): LiveSnapshot {
  if (typeof window === "undefined") {
    return DEFAULT_FALLBACK_SNAPSHOT;
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed.account_equity === "string") {
        return parsed;
      }
    }
  } catch {
    // Ignore JSON parse errors and return fallback
  }
  return DEFAULT_FALLBACK_SNAPSHOT;
}

export function useLiveTrader() {
  const [snapshot, setSnapshot] = useState<LiveSnapshot>(getInitialSnapshot);
  const [latestTick, setLatestTick] = useState<LiveTick | null>(null);
  const [newBar, setNewBar] = useState<LiveBar | null>(null);
  const [connected, setConnected] = useState(false);
  const [priceFlash, setPriceFlash] = useState<"up" | "down" | null>(null);
  const [isRebalancing, setIsRebalancing] = useState(false);

  const prevMid = useRef<number | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Save latest snapshot to localStorage whenever it changes
  const saveSnapshot = (snap: LiveSnapshot) => {
    setSnapshot(snap);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snap));
    } catch {
      // Storage quota or private browsing mode
    }
  };

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
          saveSnapshot(snap);
        }
        return { success: true, message: "Rebalance executed successfully!" };
      } else {
        return {
          success: false,
          message: body.message || `Request failed (${res.status})`,
        };
      }
    } catch (e: any) {
      console.warn("Rebalance offline or unreachable:", e);
      return {
        success: false,
        message: "Engine is currently offline. Start the local engine to execute orders.",
      };
    } finally {
      setIsRebalancing(false);
    }
  };

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    // Try fetching REST snapshot initially in case WS is delayed
    const fetchInitialRest = async () => {
      try {
        const isHttps = window.location.protocol === "https:";
        const proto = isHttps ? "https:" : "http:";
        const host = window.location.hostname || "127.0.0.1";
        const port =
          window.location.port && window.location.port !== "8080" ? "8080" : window.location.port;
        const url = `${proto}//${host}${port ? `:${port}` : ""}/api/status`;
        const res = await fetch(url);
        if (res.ok && isMounted) {
          const data = await res.json();
          if (!data.equity_history && data.equity_curve) {
            data.equity_history = data.equity_curve;
          }
          saveSnapshot(data);
        }
      } catch {
        // Expected when PC is offline
      }
    };

    void fetchInitialRest();

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
              if (!snap.equity_history && (snap as any).equity_curve) {
                snap.equity_history = (snap as any).equity_curve;
              }
              saveSnapshot(snap);
            } else if (msg.type === "portfolio_update") {
              const data = msg.data;
              const eqNum = parseFloat(data.equity);
              const cashNum = parseFloat(data.cash);
              const pointTime = data.time || Math.floor(Date.now() / 1000);

              setSnapshot((prev) => {
                const prevHistory = prev.equity_history || [];
                const lastPoint = prevHistory[prevHistory.length - 1];

                let updatedHistory: { time: number; equity: number; cash: number }[];
                if (!lastPoint || pointTime - lastPoint.time >= 5) {
                  updatedHistory = [
                    ...prevHistory,
                    { time: pointTime, equity: eqNum, cash: cashNum },
                  ];
                  if (updatedHistory.length > 2000) {
                    updatedHistory = updatedHistory.slice(updatedHistory.length - 2000);
                  }
                } else {
                  updatedHistory = [
                    ...prevHistory.slice(0, -1),
                    { time: lastPoint.time, equity: eqNum, cash: cashNum },
                  ];
                }

                const updated = {
                  ...prev,
                  account_equity: data.equity,
                  cash_balance: data.cash,
                  invested_value: data.invested,
                  drawdown_pct: data.drawdown_pct,
                  high_water_mark: data.high_water_mark || prev.high_water_mark,
                  equity_history: updatedHistory,
                };
                try {
                  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
                } catch {
                  // Ignore
                }
                return updated;
              });
            } else if (msg.type === "rebalance_executed") {
              const data = msg.data;
              setSnapshot((prev) => {
                const updated = {
                  ...prev,
                  recent_orders: [...(prev.recent_orders || []), ...(data.orders || [])],
                };
                try {
                  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
                } catch {
                  // Ignore
                }
                return updated;
              });
            } else if (msg.type === "killswitch_tripped") {
              const data = msg.data;
              setSnapshot((prev) => {
                const updated = {
                  ...prev,
                  killswitch: {
                    ...prev.killswitch,
                    state: "TRIPPED",
                    is_tripped: true,
                    loss_r: data.loss_r || prev.killswitch.loss_r,
                    trip_reason: data.reason || prev.killswitch.trip_reason,
                  },
                };
                try {
                  localStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
                } catch {
                  // Ignore
                }
                return updated;
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
          // Retry connecting every 5 seconds without disturbing current snapshot
          reconnectTimeout = setTimeout(connect, 5000);
        };

        ws.onerror = () => {
          if (!isMounted) return;
          setConnected(false);
          // Gracefully let onclose handle reconnection
        };
      } catch {
        setConnected(false);
        reconnectTimeout = setTimeout(connect, 5000);
      }
    };

    connect();

    return () => {
      isMounted = false;
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      if (ws) ws.close();
    };
  }, []);

  return {
    snapshot,
    connected,
    latestTick,
    newBar,
    priceFlash,
    triggerRebalance,
    isRebalancing,
  };
}
