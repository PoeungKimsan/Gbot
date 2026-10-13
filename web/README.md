# web/ — VAR_117 Live Trading Dashboard

A React + Vite + Tailwind dashboard for the **VAR_117 Multi-Asset ETF Momentum Rotation Engine**. It connects to the FastAPI backend at `127.0.0.1:8080` via a real-time WebSocket stream at `/ws/live` and REST snapshots under `/api/*`.

Styled per [`DESIGN-supabase.md`](../DESIGN-supabase.md) using dark, minimalist Supabase-inspired design tokens.

## Commands

```bash
npm install       # install dependencies
npm run dev       # vite dev server on :5173 (proxies /api and /ws to :8080)
npm run build     # typecheck and compile to dist/ (mounted directly by the API)
npm run typecheck # run TypeScript check (tsc --noEmit)
```

Node 20 is pinned in `.nvmrc`.

## Layout & Components

```
src/
├── App.tsx                    Main layout, NY session clock, and live status header
├── hooks/
│   └── useLiveTrader.ts       WebSocket connection, reconnection logic, snapshot state
└── components/
    ├── PortfolioSummaryCards.tsx  Equity, Cash Balance, Invested NAV, PnL, Drawdown %
    ├── ETFRadar.tsx               12-ETF momentum scores, 12M vol, and Cash Filter radar
    ├── HoldingsTable.tsx          Active portfolio weights, share quantities, and PnL
    └── RebalanceTimeline.tsx      Month-end execution schedule and audited order log
```
