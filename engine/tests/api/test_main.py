"""Tests for FastAPI real-time service (api.main).

Verifies:
1. REST endpoints (/api/status, /api/portfolio, /api/radar, /api/positions, /api/trades,
   /api/bars) serve state.
2. Rebalance trigger endpoint (/api/rebalance/execute) functions correctly.
3. WebSocket (/ws/live) yields initial snapshot on connect and receives live broadcasts.
4. CORS headers are enabled for Vite dev client.
5. Lifespan properly manages LiveTrader lifecycle.
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
        assert "strategy" in data
        assert "account_equity" in data
        assert "cash_balance" in data
        assert "killswitch" in data
        assert data["killswitch"]["state"] in ("ARMED", "TRIPPED")


def test_get_portfolio_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "account_equity" in data
        assert "cash_balance" in data
        assert "holdings" in data
        assert "rebalance_info" in data


def test_get_radar_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/radar")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)


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


def test_trigger_rebalance_endpoint() -> None:
    app = create_app()
    with TestClient(app) as client:
        # Unauthorized without PIN
        unauth_resp = client.post("/api/rebalance/execute")
        assert unauth_resp.status_code == 401

        # Authorized with correct PIN
        import os

        pin = os.getenv("ADMIN_PIN", "1234")
        with patch(
            "engine.runtime.live_trader.global_trader.execute_rebalance",
            new_callable=AsyncMock,
            return_value=[{"client_order_id": "ord-1", "ticker": "SPY", "side": "buy"}],
        ) as mock_exec:
            resp = client.post(
                "/api/rebalance/execute?force=true",
                headers={"X-Admin-Pin": pin},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "success"
            assert data["orders_count"] == 1
            mock_exec.assert_called_once_with(force=True)


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
        assert "account_equity" in init_msg["data"]

        # 2. Trader broadcasting reaches the websocket client
        global_trader.broadcast(
            "portfolio_update",
            {
                "equity": "100000.00",
                "cash": "50000.00",
            },
        )

        update_msg = ws.receive_json()
        assert update_msg["type"] == "portfolio_update"
        assert update_msg["data"]["equity"] == "100000.00"


def test_lifespan_starts_and_stops_trader() -> None:
    with (
        patch("engine.runtime.live_trader.global_trader.start", new_callable=AsyncMock) as m_start,
        patch("engine.runtime.live_trader.global_trader.stop", new_callable=AsyncMock) as m_stop,
    ):
        app = create_app()
        with TestClient(app):
            m_start.assert_called_once()
        m_stop.assert_called_once()
