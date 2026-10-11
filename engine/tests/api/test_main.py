"""Tests for FastAPI real-time service (api.main).

Verifies:
1. REST endpoints (/api/status, /api/bars, /api/positions, /api/trades) serve state.
2. WebSocket (/ws/live) yields initial snapshot on connect and receives live broadcasts.
3. CORS headers are enabled for Vite dev client.
4. Lifespan properly manages LiveTrader lifecycle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from api.main import create_app
from fastapi.testclient import TestClient


def test_get_status_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "symbol" in data
        assert "bid" in data
        assert "ask" in data
        assert "killswitch" in data
        assert data["killswitch"]["state"] in ("ARMED", "TRIPPED")


def test_get_bars_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/bars")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


def test_get_positions_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/positions")
        assert resp.status_code == 200
        data = resp.json()
        assert "positions" in data
        assert "unrealized_pnl" in data


def test_get_trades_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/trades")
        assert resp.status_code == 200
        data = resp.json()
        assert "trades" in data
        assert "realized_pnl_today" in data


def test_cors_headers_present() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.options(
            "/api/status",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_websocket_live_stream_initial_message_and_broadcast() -> None:
    app = create_app()
    from engine.runtime.live_trader import global_trader

    with TestClient(app) as client, client.websocket_connect("/ws/live") as ws:
        # 1. First message upon connection is the initial full snapshot
        init_msg = ws.receive_json()
        assert init_msg["type"] == "init"
        assert "data" in init_msg
        assert init_msg["data"]["symbol"] == global_trader.symbol

        # 2. Trader broadcasting a tick reaches the websocket client
        global_trader.broadcast(
            "tick",
            {
                "bid": "2700.50",
                "ask": "2700.70",
                "mid": "2700.60",
                "spread_ticks": 20,
                "time": 1710000000,
            },
        )

        tick_msg = ws.receive_json()
        assert tick_msg["type"] == "tick"
        assert tick_msg["data"]["bid"] == "2700.50"


def test_lifespan_starts_and_stops_trader() -> None:
    with (
        patch("engine.runtime.live_trader.global_trader.start", new_callable=AsyncMock) as m_start,
        patch("engine.runtime.live_trader.global_trader.stop", new_callable=AsyncMock) as m_stop,
    ):
        app = create_app()
        with TestClient(app):
            m_start.assert_called_once()
        m_stop.assert_called_once()
