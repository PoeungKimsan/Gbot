"""Alpaca Markets execution broker for multi-asset ETF portfolio rebalancing.

Supports paper and live trading via Alpaca REST API v2, with fractional share
support, delta rebalancing, and an explicit local mock paper fallback when
configured via BROKER=mock.

Strictly zero-float: all prices, balances, quantities, and PnL are Decimals.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import uuid
from decimal import Decimal
from typing import Any

import httpx

from engine.feed.alpaca_feed import Quote

logger = logging.getLogger(__name__)

__all__ = [
    "OPEN_ORDER_STATUSES",
    "AlpacaBroker",
    "AlpacaBrokerError",
]

OPEN_ORDER_STATUSES: tuple[str, ...] = (
    "new",
    "pending_new",
    "accepted",
    "partially_filled",
    "accepted_for_bidding",
    "calculated",
)


class AlpacaBrokerError(RuntimeError):
    """Raised when an order or broker operation fails."""


class AlpacaBroker:
    """Connects to Alpaca Markets (Paper / Live) or runs simulated mock execution."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        mode: str | None = None,
        paper_mode: bool | None = None,
        initial_mock_cash: Decimal = Decimal("100000.00"),
        mock: bool = False,
        fill_delay_polls: int = 0,
    ) -> None:
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.api_secret = api_secret or os.getenv("APCA_API_SECRET_KEY")
        self.fill_delay_polls = fill_delay_polls

        raw_mode = "mock" if mock else (mode or os.getenv("BROKER", "paper")).strip().lower()
        if raw_mode not in {"mock", "paper", "live"}:
            raise ValueError(
                f"Invalid BROKER mode '{raw_mode}'. Must be one of ('mock', 'paper', 'live')."
            )
        self.mode = raw_mode
        self.mock = (self.mode == "mock")

        if paper_mode is not None:
            self.paper_mode = paper_mode
        else:
            self.paper_mode = (self.mode != "live")

        if base_url:
            raw_url = base_url.rstrip("/")
        elif self.paper_mode:
            raw_url = os.getenv(
                "APCA_API_BASE_URL", "https://paper-api.alpaca.markets"
            ).rstrip("/")
        else:
            raw_url = "https://api.alpaca.markets"

        if raw_url.endswith("/v2"):
            raw_url = raw_url[:-3]
        self.base_url = raw_url

        # Validate paper_mode matches base_url
        if (
            self.paper_mode
            and "api.alpaca.markets" in self.base_url
            and "paper" not in self.base_url
        ):
            raise AlpacaBrokerError(
                f"Mode/URL mismatch: paper_mode is True but base_url is live endpoint "
                f"'{self.base_url}'"
            )
        if not self.paper_mode and "paper-api.alpaca.markets" in self.base_url:
            raise AlpacaBrokerError(
                f"Mode/URL mismatch: live mode active but base_url is paper endpoint "
                f"'{self.base_url}'"
            )

        # Mode enforcement: forbid silent fallback to mock
        if self.mode in {"paper", "live"}:
            if not (self.api_key and self.api_secret):
                raise AlpacaBrokerError(
                    f"BROKER='{self.mode}' requires APCA_API_KEY_ID and APCA_API_SECRET_KEY. "
                    "Silent fallback to mock is prohibited. "
                    "Explicitly configure BROKER=mock to run offline."
                )
            if self.mode == "live":
                allow_live = os.getenv("ALLOW_LIVE_TRADING", "0").lower() in ("1", "true")
                if not allow_live:
                    raise AlpacaBrokerError(
                        "Live trading is blocked: ALLOW_LIVE_TRADING flag is not set "
                        "(2026-10-30 is paper-only)."
                    )
            self.is_connected_mode = True
        else:
            self.is_connected_mode = False

        # Local mock state
        self._mock_cash = initial_mock_cash
        self._mock_positions: dict[str, dict[str, Decimal]] = {}  # ticker -> {qty, avg_price}
        self._mock_orders: list[dict[str, Any]] = []

        if not self.is_connected_mode:
            logger.info(
                "AlpacaBroker operating in explicit simulated mock mode (Cash: $%s)",
                self._mock_cash,
            )
        else:
            logger.info(
                "AlpacaBroker initialized in %s mode (endpoint: %s)",
                "PAPER" if self.paper_mode else "LIVE",
                self.base_url,
            )

    def _get_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key or "",
            "APCA-API-SECRET-KEY": self.api_secret or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def get_account_summary(self) -> dict[str, Decimal]:
        """Fetch real-time account balances (equity, cash, buying power)."""
        if not self.is_connected_mode:
            invested = sum(
                (pos["qty"] * pos["avg_price"] for pos in self._mock_positions.values()),
                Decimal("0.00"),
            )
            equity = self._mock_cash + invested
            return {
                "equity": equity,
                "cash": self._mock_cash,
                "buying_power": equity,
                "portfolio_value": invested,
            }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.base_url}/v2/account", headers=self._get_headers())
            if resp.status_code != 200:
                raise AlpacaBrokerError(
                    f"Alpaca get_account failed ({resp.status_code}): {resp.text}"
                )
            data = resp.json()
            return {
                "equity": Decimal(str(data.get("equity", "0"))),
                "cash": Decimal(str(data.get("cash", "0"))),
                "buying_power": Decimal(str(data.get("buying_power", "0"))),
                "portfolio_value": Decimal(str(data.get("portfolio_value", "0"))),
            }

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Fetch current open positions."""
        if not self.is_connected_mode:
            res: list[dict[str, Any]] = []
            for sym, pos in self._mock_positions.items():
                if pos["qty"] > Decimal("0"):
                    mkt_val = (pos["qty"] * pos["avg_price"]).quantize(Decimal("0.01"))
                    res.append({
                        "symbol": sym,
                        "qty": pos["qty"],
                        "avg_price": pos["avg_price"],
                        "avg_entry_price": pos["avg_price"],
                        "current_price": pos["avg_price"],
                        "market_value": mkt_val,
                        "unrealized_pnl": Decimal("0.00"),
                    })
            return res

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.base_url}/v2/positions", headers=self._get_headers())
            if resp.status_code != 200:
                raise AlpacaBrokerError(
                    f"Alpaca get_positions failed ({resp.status_code}): {resp.text}"
                )
            positions = resp.json()
            res_list: list[dict[str, Any]] = []
            for p in positions:
                sym = p.get("symbol")
                if not sym:
                    continue
                res_list.append({
                    "symbol": sym,
                    "qty": Decimal(str(p.get("qty", "0"))),
                    "avg_price": Decimal(str(p.get("avg_entry_price", "0"))),
                    "avg_entry_price": Decimal(str(p.get("avg_entry_price", "0"))),
                    "current_price": Decimal(str(p.get("current_price", "0"))),
                    "market_value": Decimal(str(p.get("market_value", "0"))),
                    "unrealized_pnl": Decimal(str(p.get("unrealized_pl", "0"))),
                })
            return res_list

    async def get_orders(self, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        """Retrieve recent or open orders."""
        if not self.is_connected_mode:
            if status == "open":
                return [
                    o for o in self._mock_orders
                    if o.get("status") in OPEN_ORDER_STATUSES or o.get("status") == "open"
                ][-limit:]
            return list(self._mock_orders[-limit:])

        async with httpx.AsyncClient(timeout=10) as client:
            params = {"status": status, "limit": str(limit)}
            resp = await client.get(
                f"{self.base_url}/v2/orders",
                headers=self._get_headers(),
                params=params,
            )
            if resp.status_code != 200:
                raise AlpacaBrokerError(
                    f"Alpaca get_orders failed ({resp.status_code}): {resp.text}"
                )
            return resp.json()

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch details for a single order by Alpaca order ID."""
        if not self.is_connected_mode:
            for o in self._mock_orders:
                if o.get("id") == order_id:
                    if o.get("status") == "accepted" and "_polls_left" in o:
                        o["_polls_left"] -= 1
                        if o["_polls_left"] <= 0:
                            o["status"] = "filled"
                    return o
            raise AlpacaBrokerError(f"Order '{order_id}' not found in mock store.")

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.base_url}/v2/orders/{order_id}",
                headers=self._get_headers(),
            )
            if resp.status_code != 200:
                raise AlpacaBrokerError(
                    f"Alpaca get_order failed ({resp.status_code}): {resp.text}"
                )
            return resp.json()

    async def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        """Fetch details for a single order by client_order_id, or None if not found."""
        if not self.is_connected_mode:
            for o in self._mock_orders:
                if o.get("client_order_id") == client_order_id:
                    if o.get("status") == "accepted" and "_polls_left" in o:
                        o["_polls_left"] -= 1
                        if o["_polls_left"] <= 0:
                            o["status"] = "filled"
                    return o
            return None

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.base_url}/v2/orders:by_client_order_id",
                headers=self._get_headers(),
                params={"client_order_id": client_order_id},
            )
            if resp.status_code == 404:
                return None
            if resp.status_code != 200:
                raise AlpacaBrokerError(
                    f"Alpaca get_order_by_client_id failed ({resp.status_code}): {resp.text}"
                )
            return resp.json()

    async def await_order_fill(
        self,
        order_id: str,
        timeout_seconds: int = 120,
        poll_interval_seconds: int = 1,
    ) -> dict[str, Any]:
        """Poll until order reaches terminal status or timeout."""
        start_time = dt.datetime.now(dt.UTC)
        deadline = start_time + dt.timedelta(seconds=timeout_seconds)
        while True:
            order = await self.get_order(order_id)
            status = order.get("status")
            if status in ("filled", "partially_filled"):
                return order
            if status in ("canceled", "expired", "rejected"):
                raise AlpacaBrokerError(
                    f"Order {order_id} reached terminal non-filled status: '{status}'"
                )
            if dt.datetime.now(dt.UTC) >= deadline:
                raise AlpacaBrokerError(
                    f"Order {order_id} fill confirmation timed out after {timeout_seconds}s "
                    f"(status='{status}')"
                )
            if self.mock:
                await asyncio.sleep(0)
            else:
                await asyncio.sleep(poll_interval_seconds)

    async def cancel_open_orders(self) -> int:
        """Cancel all open pending orders."""
        if not self.is_connected_mode:
            return 0

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(f"{self.base_url}/v2/orders", headers=self._get_headers())
            if resp.status_code not in (200, 207):
                logger.warning(
                    "Cancel open orders returned status %s: %s",
                    resp.status_code,
                    resp.text,
                )
                return 0
            cancelled_list = resp.json()
            return len(cancelled_list) if isinstance(cancelled_list, list) else 0

    async def rebalance_portfolio(
        self,
        target_weights: dict[str, Decimal],
        current_prices: dict[str, Quote | Decimal],
        rebalance_date: dt.date | str | None = None,
        cash_buffer: Decimal = Decimal("0.00"),
        fill_timeout_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        """Execute delta rebalancing against target portfolio allocations.

        Sizing invariants:
        1. Sizes orders from Alpaca account equity and position market_value as notional orders.
        2. Executes SELLS before BUYS.
        3. Treats a weight sum up to 1.0001 (cash clamped to 0) as rounding, not leverage.
        4. Caps total buys at available cash (initial cash + proceeds from executed sells).
        5. Supports deterministic client_order_id per (rebalance_date, symbol) for idempotency.
        """
        # Validate target weights sum
        total_w = sum(target_weights.values())
        if total_w > Decimal("1.0001"):
            raise AlpacaBrokerError(
                f"Target weight sum {total_w} exceeds 1.0001 threshold (unauthorized leverage)."
            )

        if self.is_connected_mode:
            for sym, q in current_prices.items():
                if isinstance(q, Quote) and q.source == "cache":
                    raise AlpacaBrokerError(
                        f"Refusing rebalance in {self.mode} mode: "
                        f"quote for {sym} has source='cache'. "
                        "Live and paper execution mandate live quotes."
                    )

        if rebalance_date is not None:
            reb_date_str = (
                rebalance_date.isoformat()
                if isinstance(rebalance_date, dt.date)
                else str(rebalance_date)
            )
            client_id_prefix = f"var117-{reb_date_str}"
        else:
            client_id_prefix = f"var117-{uuid.uuid4().hex[:8]}"

        account_info = await self.get_account_summary()
        total_equity = account_info["equity"]
        cash_available = account_info["cash"]

        raw_positions = await self.get_open_positions()
        pos_by_symbol = {p["symbol"]: p for p in raw_positions}

        # Check pending and existing orders to avoid duplicate executions
        pending_symbols: set[str] = set()
        existing_client_order_ids: set[str] = set()
        open_orders = await self.get_orders(status="open")
        for o in open_orders:
            sym = o.get("symbol")
            if sym:
                pending_symbols.add(sym)
            cid = o.get("client_order_id")
            if cid:
                existing_client_order_ids.add(cid)

        all_orders = await self.get_orders(status="all", limit=100)
        for o in all_orders:
            cid = o.get("client_order_id")
            if cid:
                existing_client_order_ids.add(cid)

        executed_orders: list[dict[str, Any]] = []

        all_symbols = set(target_weights.keys()).union(set(pos_by_symbol.keys()))
        sells: list[dict[str, Any]] = []
        buys: list[dict[str, Any]] = []

        for symbol in sorted(all_symbols):
            client_id = f"{client_id_prefix}-{symbol}"
            if client_id in existing_client_order_ids:
                logger.info(
                    "Order %s already exists in broker orders; skipping duplicate.",
                    client_id,
                )
                continue

            existing_order = await self.get_order_by_client_id(client_id)
            if existing_order is not None:
                logger.info(
                    "Order %s found via client_order_id query; skipping duplicate.",
                    client_id,
                )
                existing_client_order_ids.add(client_id)
                continue

            if symbol in pending_symbols:
                logger.info(
                    "Order for %s is already pending in broker queue; skipping duplicate.",
                    symbol,
                )
                continue

            price_val = current_prices.get(symbol)
            if isinstance(price_val, Quote):
                price = price_val.price
            elif isinstance(price_val, Decimal):
                price = price_val
            else:
                price = None

            if price is None or price <= Decimal("0"):
                if symbol in pos_by_symbol:
                    price = pos_by_symbol[symbol]["current_price"]
                else:
                    logger.warning("No price available for %s; skipping in rebalance.", symbol)
                    continue

            current_pos = pos_by_symbol.get(symbol)
            current_qty = current_pos["qty"] if current_pos else Decimal("0")
            current_val = current_pos["market_value"] if current_pos else Decimal("0.00")

            target_w = target_weights.get(symbol, Decimal("0.0000"))
            target_val = (total_equity * target_w).quantize(Decimal("0.01"))

            delta_val = target_val - current_val

            # Full liquidation when target is zero
            if target_val <= Decimal("0") and current_qty > Decimal("0"):
                sells.append({
                    "symbol": symbol,
                    "side": "sell",
                    "qty": current_qty,
                    "notional": current_val,
                    "price": price,
                    "client_order_id": client_id,
                })
            elif delta_val < Decimal("-5.00"):
                # Partial reduction
                sell_notional = abs(delta_val).quantize(Decimal("0.01"))
                sell_qty = (sell_notional / price).quantize(Decimal("0.0001"))
                sell_qty = min(sell_qty, current_qty)
                if sell_qty > Decimal("0"):
                    sells.append({
                        "symbol": symbol,
                        "side": "sell",
                        "qty": sell_qty,
                        "notional": sell_notional,
                        "price": price,
                        "client_order_id": client_id,
                    })
            elif delta_val > Decimal("5.00"):
                # Buy
                buy_notional = delta_val.quantize(Decimal("0.01"))
                buy_qty = (buy_notional / price).quantize(Decimal("0.0001"))
                if buy_qty > Decimal("0"):
                    buys.append({
                        "symbol": symbol,
                        "side": "buy",
                        "qty": buy_qty,
                        "notional": buy_notional,
                        "price": price,
                        "client_order_id": client_id,
                    })

        # 1. Execute SELLS first to free cash
        realized_sell_dollars = Decimal("0.00")
        sell_errors: list[str] = []
        for order_spec in sells:
            try:
                order_res = await self._place_order(
                    symbol=order_spec["symbol"],
                    qty=order_spec["qty"],
                    notional=order_spec["notional"],
                    side="sell",
                    price=order_spec["price"],
                    client_order_id=order_spec["client_order_id"],
                )
                needs_fill_wait = (
                    (self.is_connected_mode or self.fill_delay_polls > 0)
                    and order_res.get("status") != "filled"
                )
                if needs_fill_wait:
                    order_res = await self.await_order_fill(
                        order_res["id"],
                        timeout_seconds=fill_timeout_seconds,
                    )
                executed_orders.append(order_res)
                filled_val = Decimal(str(order_res.get("notional", order_spec["notional"])))
                realized_sell_dollars += filled_val
            except Exception as err:
                logger.error("Failed to execute sell for %s: %s", order_spec["symbol"], err)
                sell_errors.append(f"{order_spec['symbol']}: {err}")
                break

        if sell_errors:
            raise AlpacaBrokerError(
                f"Rebalance aborted during sell phase: {'; '.join(sell_errors)}"
            )

        # 2. Cap BUYS at available cash with cash buffer
        effective_cash = max(Decimal("0.00"), cash_available + realized_sell_dollars - cash_buffer)
        total_requested_buys = sum((b["notional"] for b in buys), Decimal("0.00"))

        if total_requested_buys > effective_cash:
            if effective_cash > Decimal("0.00"):
                scale_ratio = effective_cash / total_requested_buys
                logger.warning(
                    "Requested buys ($%s) exceed available cash with buffer ($%s). "
                    "Scaling buys by %s",
                    total_requested_buys,
                    effective_cash,
                    scale_ratio.quantize(Decimal("0.0001")),
                )
                for b in buys:
                    scaled_notional = (b["notional"] * scale_ratio).quantize(Decimal("0.01"))
                    b["notional"] = scaled_notional
                    b["qty"] = (scaled_notional / b["price"]).quantize(Decimal("0.0001"))
            else:
                logger.warning("Zero cash available for buys after buffer ($%s).", effective_cash)
                buys = []

        # 3. Execute BUYS second
        buy_errors: list[str] = []
        for order_spec in buys:
            if order_spec["qty"] <= Decimal("0") or order_spec["notional"] <= Decimal("0"):
                continue
            try:
                order_res = await self._place_order(
                    symbol=order_spec["symbol"],
                    qty=order_spec["qty"],
                    notional=order_spec["notional"],
                    side="buy",
                    price=order_spec["price"],
                    client_order_id=order_spec["client_order_id"],
                )
                needs_fill_wait = (
                    (self.is_connected_mode or self.fill_delay_polls > 0)
                    and order_res.get("status") != "filled"
                )
                if needs_fill_wait:
                    order_res = await self.await_order_fill(
                        order_res["id"],
                        timeout_seconds=fill_timeout_seconds,
                    )
                executed_orders.append(order_res)
            except Exception as err:
                logger.error("Failed to execute buy for %s: %s", order_spec["symbol"], err)
                buy_errors.append(f"{order_spec['symbol']}: {err}")

        if buy_errors:
            raise AlpacaBrokerError(
                f"Rebalance completed with buy errors: {'; '.join(buy_errors)}"
            )

        logger.info(
            "Rebalance completed: %d orders submitted (%d sells, %d buys).",
            len(executed_orders),
            len(sells),
            len(buys),
        )
        return executed_orders

    submit_rebalance_orders = rebalance_portfolio

    async def _place_order(
        self,
        symbol: str,
        qty: Decimal,
        side: str,
        price: Decimal,
        notional: Decimal | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit an order with optional notional value and client_order_id."""
        if not self.is_connected_mode:
            # Mock idempotency: reuse existing order if client_order_id matches
            if client_order_id:
                for existing in self._mock_orders:
                    if existing.get("client_order_id") == client_order_id:
                        logger.info(
                            "Order %s already executed in mock; returning existing.",
                            client_order_id,
                        )
                        return existing

            order_id = str(uuid.uuid4())
            trade_value = notional if notional is not None else (qty * price)

            if side == "sell":
                self._mock_cash += trade_value
                current_p = self._mock_positions.get(symbol)
                if current_p:
                    new_qty = current_p["qty"] - qty
                    if new_qty <= Decimal("0.0001"):
                        del self._mock_positions[symbol]
                    else:
                        current_p["qty"] = new_qty
            else:  # buy
                self._mock_cash -= trade_value
                current_p = self._mock_positions.get(symbol)
                if current_p:
                    total_shares = current_p["qty"] + qty
                    weighted_p = (
                        current_p["qty"] * current_p["avg_price"] + trade_value
                    ) / total_shares
                    current_p["qty"] = total_shares
                    current_p["avg_price"] = weighted_p
                else:
                    self._mock_positions[symbol] = {
                        "qty": qty,
                        "avg_price": price,
                    }

            initial_status = "accepted" if self.fill_delay_polls > 0 else "filled"
            order_record = {
                "id": order_id,
                "client_order_id": client_order_id,
                "symbol": symbol,
                "qty": str(qty),
                "notional": str(trade_value),
                "side": side,
                "type": "market",
                "filled_avg_price": str(price),
                "status": initial_status,
                "_polls_left": self.fill_delay_polls,
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "mode": "simulated_paper",
            }
            self._mock_orders.append(order_record)
            return order_record

        # Connected Alpaca API call
        async with httpx.AsyncClient(timeout=10) as client:
            payload: dict[str, Any] = {
                "symbol": symbol,
                "side": side,
                "type": "market",
                "time_in_force": "day",
            }
            if notional is not None and notional > Decimal("0"):
                payload["notional"] = str(notional)
            else:
                payload["qty"] = str(qty)

            if client_order_id:
                payload["client_order_id"] = client_order_id

            resp = await client.post(
                f"{self.base_url}/v2/orders",
                headers=self._get_headers(),
                json=payload,
            )
            if resp.status_code not in (200, 201):
                raise AlpacaBrokerError(
                    f"Alpaca order placement failed for {symbol} {side} "
                    f"(notional={notional}, qty={qty}): {resp.status_code} - {resp.text}"
                )
            res_json = resp.json()
            return {
                "id": str(res_json.get("id")),
                "client_order_id": str(res_json.get("client_order_id", client_order_id)),
                "symbol": str(res_json.get("symbol")),
                "qty": str(res_json.get("qty", qty)),
                "side": str(res_json.get("side", side)),
                "type": str(res_json.get("type", "market")),
                "status": str(res_json.get("status", "accepted")),
                "created_at": str(
                    res_json.get("created_at", dt.datetime.now(dt.UTC).isoformat())
                ),
                "mode": "alpaca_api",
            }

    async def liquidate_all_positions(self) -> list[dict[str, Any]]:
        """Emergency liquidation (used when Kill-Switch trips)."""
        if not self.is_connected_mode:
            closed: list[dict[str, Any]] = []
            for sym, p in list(self._mock_positions.items()):
                self._mock_cash += p["qty"] * p["avg_price"]
                closed.append({"symbol": sym, "qty": str(p["qty"]), "status": "liquidated"})
            self._mock_positions.clear()
            return closed

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.delete(
                f"{self.base_url}/v2/positions",
                headers=self._get_headers(),
            )
            if resp.status_code not in (200, 207):
                logger.error("Failed to liquidate Alpaca positions: %s", resp.text)
                return []
            return resp.json()
