# web/ — the engine dashboard

A read-only React projection of the trading journal. It consumes exactly one thing: the
relative `/api/*` endpoints served by `api/main.py` (`127.0.0.1:8080`), which in turn reads
the publisher's atomic snapshots and opens nothing else. The dashboard has no origin, host
or port in its code, because it is served by the same origin as the API.

## Commands

```bash
npm ci            # what CI runs; installs from the committed lockfile
npm run dev       # vite dev server on :5173, proxying /api to :8080
npm run build     # typecheck, then emit dist/  ->  web/dist
npm run test      # vitest run
npm run typecheck # tsc --noEmit, on its own
```

Node 20 is pinned in `.nvmrc`, and `package.json` declares `engines: { node: ">=20" }`.

## Looking at it before the engine publishes everything

`vite dev` with `?fixtures` in the URL renders `src/api/fixtures.ts` instead of the API, and
carries a loud "Fixture data — not the engine" panel. The fixtures include the Phase 5.x
fields the current engine build does not publish, so this is a preview of the dashboard once
`docs/PHASE_5X_SPEC.md` has been implemented. The affordance is guarded by
`import.meta.env.DEV`, which Vite replaces with `false` at build time, so Rollup drops the
dynamic import — `dist/` contains no fixture string.

## What is not published yet

Three of the six views need snapshot fields the engine does not emit:

| View | Waiting on |
| --- | --- |
| Status header | `state.json.state` — the engine never folds `feed_state_changed` |
| Trade tape | `stop_loss`, `take_profit`, `slippage` per closed trade |
| Setup visualizer | `setups.json` — bars, sweep, MSS, FVG, session levels |
| Calendar radar | `calendar.json` — Tier-1 events and blackout windows |

`docs/PHASE_5X_SPEC.md` (at the repository root, in `docs/`) specifies every one of them:
name, type, endpoint, and whether the journal already records it. Until they land, the
affected cells render `NOT PUBLISHED BY THIS ENGINE BUILD` — never a blank, never a zero, and
never a default.

## The two rules the suite exists to protect

1. **`N < 30` shows no confidence interval.** The engine refuses to bootstrap an interval
   below 30 closed trades (`engine/metrics/performance.py`), because an interval over a
   handful of trades is fabricated precision. The dashboard repeats the refusal in the
   operator's own words — `INSUFFICIENT DATA (<30 TRADES)` — and re-checks the count itself
   rather than trusting the payload's `status` string. A snapshot claiming `COMPLETE` over
   twelve trades is caught, not believed.
2. **The disclosure renders verbatim, adjacent to every performance panel.**
   `src/content/disclosure.txt` is the only source; `vite.config.ts` fails the build if it is
   missing or empty, and `src/content/disclosure.ts` refuses to load independently. Delete it
   or blank it and `npm run build` exits non-zero with a named error.

## Layout

```
src/
├── api/            the wire contract, the client, and the dev fixtures
│   ├── contract.ts    types + constants; nullable = Phase 5.x, absent today
│   ├── client.ts      relative-endpoint fetch and total normalizers
│   └── fixtures.ts    dev-only, full-contract fixtures
├── components/     one file per view, plus Panel/Disclosure/NotPublished
├── content/        disclosure.txt and its loader
├── hooks/          useNow (the only timers), useEngineSnapshot (the poll)
└── lib/            decimal.ts (BigInt money and ticks), format.ts (display only)
```

Motion is transform and opacity only, on one `cubic-bezier(0.32, 0.72, 0, 1)` curve; the
heavy blur lives on the fixed backdrop, never on a scrolling container.
