"""Real-time FastAPI service for the XAUUSD Trading Engine.

Serves live WebSocket price streams, candlestick data, active positions,
and REST snapshot endpoints for the high-performance trading dashboard.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from engine.runtime.live_trader import global_trader

logger = logging.getLogger(__name__)

__all__ = [
    "API_HOST",
    "API_PORT",
    "create_app",
]

API_HOST: Final[str] = "127.0.0.1"
API_PORT: Final[int] = 8080


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the live market data and strategy engine on API launch."""
    logger.info("Initializing Live Trading Engine...")
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
        title="XAUUSD Live Trading Engine",
        description="Real-time WebSocket & REST gateway for automated gold trading.",
        version="1.0.0",
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

    @app.get("/api/bars")
    async def get_bars() -> JSONResponse:
        """Historical M5 bars for TradingView chart."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(content=snapshot.get("recent_bars", []))

    @app.get("/api/positions")
    async def get_positions() -> JSONResponse:
        """Live open positions on MT5."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(
            content={
                "positions": snapshot.get("open_positions", []),
                "unrealized_pnl": snapshot.get("unrealized_pnl", "0.00"),
            }
        )

    @app.get("/api/trades")
    async def get_trades() -> JSONResponse:
        """Trade execution tape."""
        snapshot = global_trader.get_snapshot()
        return JSONResponse(
            content={
                "trades": snapshot.get("completed_trades", []),
                "realized_pnl_today": snapshot.get("realized_pnl_today", "0.00"),
            }
        )

    @app.websocket("/ws/live")
    async def websocket_live_stream(websocket: WebSocket) -> None:
        """Sub-50ms live stream for ticks, candles, positions, and strategy signals."""
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

    # Mount static dashboard build if present
    web_dist = Path("web/dist")
    if web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")

    return app


app = create_app()
