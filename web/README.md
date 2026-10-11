# web/ — the live trading dashboard

A React + Vite + Tailwind dashboard for the MT5 live trader. It reads the live state from
`api/main.py` (`127.0.0.1:8080`): a WebSocket stream at `/ws/live` for ticks, bars,
positions and signals, plus REST snapshots under `/api/*`.

## Commands

```bash
npm ci            # installs from the committed lockfile
npm run dev       # vite dev server on :5173, proxying /api to :8080
npm run build     # typecheck, then emit dist/  ->  web/dist (mounted by the API)
npm run typecheck # tsc --noEmit, on its own
```

Node 20 is pinned in `.nvmrc`. The visual language follows `DESIGN-supabase.md` at the
repository root.

## Layout

```
src/
├── App.tsx            page layout
├── hooks/
│   └── useLiveTrader.ts   WebSocket connection + snapshot state
└── components/
    ├── LiveTickerCard.tsx    bid/ask/spread hero
    ├── TradingViewChart.tsx  M5 candles + Asian range (lightweight-charts)
    ├── LivePositionPnL.tsx   open positions and today's PnL
    ├── StrategyRadar.tsx     Asian range and latest Judas Swing signal
    └── LiveTradeTape.tsx     executed trades
```
