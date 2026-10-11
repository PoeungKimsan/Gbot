/**
 * Real-time WebSocket hook connecting the dashboard directly to the LiveTrader stream.
 *
 * Provides sub-50ms live ticks, candlestick bar updates, real-time floating PnL,
 * active positions, and strategy execution signals with auto-reconnect.
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

export interface LiveSnapshot {
  symbol: string;
  bid: string;
  ask: string;
  mid: string;
  spread_ticks: string;
  daily_change_pct: string;
  daily_high: string;
  daily_low: string;
  account_balance: string;
  account_equity: string;
  unrealized_pnl: string;
  realized_pnl_today: string;
  open_positions: LivePosition[];
  completed_trades: LiveTrade[];
  recent_bars: LiveBar[];
  asian_range: AsianRange | null;
  latest_signal: Record<string, unknown> | null;
  updated_at: string;
}

export interface LiveTick {
  bid: string;
  ask: string;
  mid: string;
  spread_ticks: number;
  time: number;
}

export function useLiveTrader() {
  const [snapshot, setSnapshot] = useState<LiveSnapshot | null>(null);
  const [latestTick, setLatestTick] = useState<LiveTick | null>(null);
  const [newBar, setNewBar] = useState<LiveBar | null>(null);
  const [connected, setConnected] = useState(false);
  const [priceFlash, setPriceFlash] = useState<"up" | "down" | null>(null);

  const prevMid = useRef<number | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
    let isMounted = true;

    const connect = () => {
      // Determine protocol and host
      const isHttps = window.location.protocol === "https:";
      const wsProto = isHttps ? "wss:" : "ws:";
      const host = window.location.hostname || "127.0.0.1";

      let wsUrl: string;
      if (window.location.port && window.location.port !== "8080") {
        // Local dev (e.g. Vite on 5173), target local backend on 8080
        wsUrl = `ws://${host}:8080/ws/live`;
      } else if (window.location.port === "8080") {
        // Local direct access on port 8080
        wsUrl = `${wsProto}//${host}:8080/ws/live`;
      } else {
        // Production domain via Cloudflare Tunnel or reverse proxy (standard 443/80, no port in URL)
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
              if (snap.bid && snap.ask) {
                setLatestTick({
                  bid: snap.bid,
                  ask: snap.ask,
                  mid: snap.mid,
                  spread_ticks: parseInt(snap.spread_ticks, 10) || 0,
                  time: Math.floor(Date.now() / 1000),
                });
              }
            } else if (msg.type === "tick") {
              const tick = msg.data as LiveTick;
              setLatestTick(tick);

              // Update snapshot top of book
              setSnapshot((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  bid: tick.bid,
                  ask: tick.ask,
                  mid: tick.mid,
                  spread_ticks: String(tick.spread_ticks),
                };
              });

              // Price flash micro-animation
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
              const bar = msg.data as LiveBar;
              setNewBar(bar);
              setSnapshot((prev) => {
                if (!prev) return prev;
                const nextBars = [...prev.recent_bars, bar].slice(-120);
                return { ...prev, recent_bars: nextBars };
              });
            } else if (msg.type === "positions") {
              const { positions, unrealized_pnl } = msg.data;
              setSnapshot((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  open_positions: positions,
                  unrealized_pnl,
                };
              });
            }
          } catch (e) {
            console.error("Error parsing WebSocket event:", e);
          }
        };

        ws.onclose = () => {
          if (!isMounted) return;
          setConnected(false);
          // Try reconnecting in 2 seconds
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
  };
}
