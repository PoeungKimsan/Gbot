"""Real-time FastAPI service for the VAR_117 Multi-Asset ETF Trading Engine.

Serves live WebSocket portfolio streams, holdings, 12-ETF momentum radar,
and REST snapshot endpoints for the high-performance trading dashboard.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from engine.runtime.live_trader import global_trader

logger = logging.getLogger(__name__)

__all__ = [
    "API_HOST",
    "API_PORT",
    "create_app",
]

API_HOST: Final[str] = "127.0.0.1"
API_PORT: Final[int] = 8080

_HISTORY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL_SECONDS: Final[float] = 120.0


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the live market data and strategy engine on API launch."""
    logger.info("Initializing VAR_117 Live Trading Engine...")
    try:
        await global_trader.start()
    except Exception as exc:
        logger.error("Error starting LiveTrader on API launch: %s", exc)
    yield
    logger.info("Shutting down Live Trading Engine...")
    await global_trader.stop()


def create_app() -> FastAPI:
    """Build the live trading FastAPI application."""
    app = FastAPI(
        title="VAR_117 Multi-Asset ETF Trading Engine",
        description="Real-time WebSocket & REST gateway for systematic ETF momentum rotation.",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Enable CORS for local Vite dev server
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/status")
    async def get_status() -> JSONResponse:
        """Complete live engine snapshot."""
        return JSONResponse(content=global_trader.get_snapshot())

    @app.get("/api/portfolio")
    async def get_portfolio() -> JSONResponse:
        """Portfolio allocation, holdings, and cash metrics."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(
            content={
                "account_equity": snapshot.get("account_equity", "0.00"),
                "cash_balance": snapshot.get("cash_balance", "0.00"),
                "invested_value": snapshot.get("invested_value", "0.00"),
                "unrealized_pnl": snapshot.get("unrealized_pnl", "0.00"),
                "drawdown_pct": snapshot.get("drawdown_pct", "0.00"),
                "holdings": snapshot.get("holdings", []),
                "rebalance_info": snapshot.get("rebalance_info", {}),
            }
        )

    @app.get("/api/radar")
    async def get_radar() -> JSONResponse:
        """12-ETF momentum scores, volatilities, and Cash Filter status."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(content=snapshot.get("radar", []))

    @app.get("/api/positions")
    async def get_positions() -> JSONResponse:
        """Open holdings list (compatible with legacy endpoint)."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(
            content={
                "positions": snapshot.get("holdings", []),
                "unrealized_pnl": snapshot.get("unrealized_pnl", "0.00"),
            }
        )

    @app.get("/api/trades")
    async def get_trades() -> JSONResponse:
        """Rebalance execution order history."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(
            content={
                "trades": snapshot.get("recent_orders", []),
                "realized_pnl_today": snapshot.get("realized_pnl_today", "0.00"),
            }
        )

    @app.get("/api/bars")
    async def get_bars() -> JSONResponse:
        """Historical portfolio NAV / equity curve data (raw list)."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(content=snapshot.get("equity_history", []))

    @app.get("/api/portfolio/history")
    async def get_portfolio_history(
        period: str = "2D",
        timeframe: str = "5Min",
    ) -> JSONResponse:
        """Historical portfolio NAV / equity curve data matching requested period and timeframe."""
        cache_key = f"{period.upper()}:{timeframe}"
        now_mono = time.monotonic()
        if cache_key in _HISTORY_CACHE:
            cached_time, cached_payload = _HISTORY_CACHE[cache_key]
            if now_mono - cached_time < _CACHE_TTL_SECONDS:
                return JSONResponse(content=cached_payload)

        if global_trader.broker is not None:
            try:
                tf_map = {
                    "1D": "5Min",
                    "2D": "5Min",
                    "1W": "15Min",
                    "1M": "1D",
                    "3M": "1D",
                    "1Y": "1D",
                    "ALL": "1D",
                }
                actual_tf = tf_map.get(period.upper(), timeframe)
                broker_history = await global_trader.broker.get_portfolio_history(
                    period=period,
                    timeframe=actual_tf,
                )
                if broker_history:
                    formatted = [
                        {
                            "time": int(pt["timestamp"]),
                            "equity": float(pt["equity"]),
                            "profit_loss": float(pt.get("profit_loss", 0)),
                            "profit_loss_pct": float(pt.get("profit_loss_pct", 0)),
                        }
                        for pt in broker_history
                    ]
                    payload = {
                        "period": period,
                        "timeframe": actual_tf,
                        "data": formatted,
                    }
                    _HISTORY_CACHE[cache_key] = (now_mono, payload)
                    return JSONResponse(content=payload)
            except Exception as exc:
                logger.warning("Could not fetch portfolio history from broker: %s", exc)

        # Fallback to LiveTrader in-memory recorded equity history
        snapshot = global_trader.get_snapshot()
        raw_hist = snapshot.get("equity_history", [])
        return JSONResponse(
            content={
                "period": period,
                "timeframe": timeframe,
                "data": [
                    {
                        "time": int(pt["time"]),
                        "equity": float(pt["equity"]),
                        "cash": float(pt.get("cash", 0)),
                    }
                    for pt in raw_hist
                ],
            }
        )

    @app.post("/api/rebalance/execute")
    async def trigger_rebalance(request: Request) -> JSONResponse:
        """Trigger systematic rebalance evaluation and order execution.

        Protected by ADMIN_PIN and cooldown timer to prevent public spam on Cloudflare Tunnels.
        """
        # 1. Admin PIN verification
        expected_pin = os.getenv("ADMIN_PIN", "1234")
        provided_pin = request.headers.get("X-Admin-Pin") or request.query_params.get("pin")

        if not provided_pin or not secrets.compare_digest(provided_pin, expected_pin):
            return JSONResponse(
                status_code=401,
                content={
                    "status": "unauthorized",
                    "message": "Unauthorized: Invalid or missing Admin PIN.",
                },
            )

        # 2. Check degraded state
        if global_trader.is_degraded:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "message": (
                        f"Trading refused: engine is in degraded state "
                        f"({global_trader.degraded_reason})."
                    ),
                },
            )

        # 3. Check force flag
        force_str = request.query_params.get("force", "false").lower()
        force = force_str in ("true", "1", "yes")

        try:
            orders = await global_trader.execute_rebalance(force=force)
            return JSONResponse(
                content={
                    "status": "success",
                    "orders_count": len(orders),
                    "orders": orders,
                }
            )
        except RuntimeError as exc:
            if "cooldown" in str(exc).lower():
                return JSONResponse(
                    status_code=429,
                    content={
                        "status": "cooldown",
                        "message": str(exc),
                    },
                )
            return JSONResponse(
                status_code=400,
                content={
                    "status": "refused",
                    "message": str(exc),
                },
            )
        except Exception as exc:
            logger.error("Error executing rebalance: %s", exc)
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "message": str(exc),
                },
            )

    @app.websocket("/ws/live")
    async def websocket_live_stream(websocket: WebSocket) -> None:
        """Sub-50ms live stream for portfolio ticks, holdings, and rebalance signals."""
        await websocket.accept()
        queue = global_trader.subscribe()

        try:
            # Send initial full state immediately upon connect
            initial_state = {
                "type": "init",
                "data": global_trader.get_snapshot(),
            }
            await websocket.send_json(initial_state)

            while True:
                # Forward live updates from the trader broadcast queue
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug("WebSocket client disconnected: %s", exc)
        finally:
            global_trader.unsubscribe(queue)

    @app.get("/robots.txt", response_class=PlainTextResponse)
    async def get_robots_txt() -> PlainTextResponse:
        """Serve robots.txt for search engine crawlers."""
        robots_file = Path("web/dist/robots.txt")
        if not robots_file.is_file():
            robots_file = Path("web/public/robots.txt")
        if robots_file.is_file():
            return PlainTextResponse(content=robots_file.read_text(encoding="utf-8"))
        return PlainTextResponse(
            content="User-agent: *\nAllow: /\n\nSitemap: https://sanalgo.kimsan.dev/sitemap.xml\n"
        )

    @app.get("/sitemap.xml")
    async def get_sitemap_xml() -> Response:
        """Serve sitemap.xml for search engine crawlers."""
        sitemap_file = Path("web/dist/sitemap.xml")
        if not sitemap_file.is_file():
            sitemap_file = Path("web/public/sitemap.xml")
        if sitemap_file.is_file():
            return Response(
                content=sitemap_file.read_text(encoding="utf-8"),
                media_type="application/xml",
            )
        xml_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            '  <url>\n'
            '    <loc>https://sanalgo.kimsan.dev/</loc>\n'
            '    <lastmod>2026-10-17</lastmod>\n'
            '    <changefreq>daily</changefreq>\n'
            '    <priority>1.0</priority>\n'
            '  </url>\n'
            '</urlset>\n'
        )
        return Response(content=xml_content, media_type="application/xml")

    # Mount static dashboard build if present
    web_dist = Path("web/dist")
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")

    return app


app = create_app()
