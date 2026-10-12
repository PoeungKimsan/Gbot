"""Alpaca Markets execution broker for multi-asset ETF portfolio rebalancing.

Supports paper and live trading via Alpaca REST API v2, with fractional share
support, delta rebalancing, and an automated local mock paper fallback when
API credentials are not configured.

Strictly zero-float: all prices, balances, quantities, and PnL are Decimals.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import uuid
from decimal import Decimal
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

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
    """Connects to Alpaca Markets (Paper / Live) for portfolio execution."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        paper_mode: bool = True,
        initial_mock_cash: Decimal = Decimal("100000.00"),
    ) -> None:
        self.api_key = api_key or os.getenv("APCA_API_KEY_ID")
        self.api_secret = api_secret or os.getenv("APCA_API_SECRET_KEY")
        self.paper_mode = paper_mode

        if base_url:
            raw_url = base_url.rstrip("/")
        elif paper_mode:
            raw_url = os.getenv(
                "APCA_API_BASE_URL", "https://paper-api.alpaca.markets"
            ).rstrip("/")
        else:
            raw_url = "https://api.alpaca.markets"

        if raw_url.endswith("/v2"):
            raw_url = raw_url[:-3]
        self.base_url = raw_url

        # Determine if running in live/connected mode or local mock simulation
        self.is_connected_mode = bool(self.api_key and self.api_secret)

        # Local mock state when API credentials are not provided
        self._mock_cash = initial_mock_cash
        self._mock_positions: dict[str, dict[str, Decimal]] = {}  # ticker -> {qty, avg_price}
        self._mock_orders: list[dict[str, Any]] = []

        if not self.is_connected_mode:
            logger.info(
                "Alpaca credentials unset; operating in local simulated paper mode (Cash: $%s)",
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
            # Calculate mock equity
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
                err_msg = f"Failed to fetch account: {resp.status_code} - {resp.text}"
                raise AlpacaBrokerError(err_msg)
            data = resp.json()

            return {
                "equity": Decimal(str(data.get("equity", "0.00"))),
                "cash": Decimal(str(data.get("cash", "0.00"))),
                "buying_power": Decimal(str(data.get("buying_power", "0.00"))),
                "portfolio_value": Decimal(str(data.get("portfolio_value", "0.00"))),
            }

    async def get_open_positions(self) -> list[dict[str, Any]]:
        """Fetch currently held ETF positions with market prices and unrealized PnL."""
        if not self.is_connected_mode:
            positions: list[dict[str, Any]] = []
            for ticker, p in self._mock_positions.items():
                if p["qty"] > Decimal("0"):
                    positions.append(
                        {
                            "symbol": ticker,
                            "qty": p["qty"],
                            "avg_entry_price": p["avg_price"],
                            "current_price": p["avg_price"],
                            "market_value": p["qty"] * p["avg_price"],
                            "unrealized_pl": Decimal("0.00"),
                            "unrealized_plpc": Decimal("0.00"),
                        }
                    )
            return positions

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.base_url}/v2/positions",
                headers=self._get_headers(),
            )
            if resp.status_code != 200:
                raise AlpacaBrokerError(
                    f"Failed to fetch positions: {resp.status_code} - {resp.text}"
                )
            items = resp.json()

            results: list[dict[str, Any]] = []
            for item in items:
                results.append(
                    {
                        "symbol": str(item["symbol"]),
                        "qty": Decimal(str(item["qty"])),
                        "avg_entry_price": Decimal(str(item["avg_entry_price"])),
                        "current_price": Decimal(str(item["current_price"])),
                        "market_value": Decimal(str(item["market_value"])),
                        "unrealized_pl": Decimal(str(item["unrealized_pl"])),
                        "unrealized_plpc": Decimal(str(item["unrealized_plpc"])),
                    }
                )
            return results

    async def get_open_orders(self) -> list[dict[str, Any]]:
        """Fetch currently pending/unfilled orders from Alpaca."""
        if not self.is_connected_mode:
            return [
                o for o in self._mock_orders
                if str(o.get("status")) in OPEN_ORDER_STATUSES
            ]

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.base_url}/v2/orders?status=open",
                headers=self._get_headers(),
            )
            if resp.status_code != 200:
                logger.warning("Failed to fetch open orders: %s", resp.text)
                return []
            return resp.json()

    async def get_orders(self, status: str = "all", limit: int = 50) -> list[dict[str, Any]]:
        """Fetch orders history from Alpaca broker."""
        if not self.is_connected_mode:
            return list(reversed(self._mock_orders[-limit:]))

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.base_url}/v2/orders?status={status}&limit={limit}",
                headers=self._get_headers(),
            )
            if resp.status_code != 200:
                logger.warning("Failed to fetch orders: %s", resp.text)
                return []
            return resp.json()

    async def submit_rebalance_orders(
        self,
        target_weights: dict[str, Decimal],
        current_prices: dict[str, Decimal],
    ) -> list[dict[str, Any]]:
        """Execute delta rebalancing orders to match target portfolio weights.

        Sells positions with excess allocation first to free up buying power,
        then buys positions that require additional allocation.
        """
        account = await self.get_account_summary()
        total_equity = account["equity"]

        if total_equity <= Decimal("0"):
            logger.warning("Total equity is non-positive ($%s); rebalance aborted.", total_equity)
            return []

        # Check existing positions and pending unfilled orders
        existing_positions = await self.get_open_positions()
        pos_by_symbol = {p["symbol"]: p for p in existing_positions}

        open_orders = await self.get_open_orders()
        pending_symbols = {
            str(o.get("symbol"))
            for o in open_orders
            if str(o.get("status")) in OPEN_ORDER_STATUSES
        }

        executed_orders: list[dict[str, Any]] = []

        # Calculate target dollar value and required delta per asset
        all_symbols = set(target_weights.keys()).union(set(pos_by_symbol.keys()))
        sells: list[dict[str, Any]] = []
        buys: list[dict[str, Any]] = []

        for symbol in sorted(all_symbols):
            if symbol in pending_symbols:
                logger.info(
                    "Order for %s is already pending in broker queue; skipping duplicate.",
                    symbol,
                )
                continue
            price = current_prices.get(symbol)
            if price is None or price <= Decimal("0"):
                if symbol in pos_by_symbol:
                    price = pos_by_symbol[symbol]["current_price"]
                else:
                    logger.warning("No price available for %s; skipping in rebalance.", symbol)
                    continue

            current_pos = pos_by_symbol.get(symbol)
            current_qty = current_pos["qty"] if current_pos else Decimal("0")
            current_val = current_qty * price

            target_w = target_weights.get(symbol, Decimal("0.0000"))
            target_val = (total_equity * target_w).quantize(Decimal("0.01"))

            delta_val = target_val - current_val

            # When target weight is 0, liquidate entire holding
            # (bypassing $5 threshold to clear dust)
            if target_val <= Decimal("0") and current_qty > Decimal("0"):
                sells.append(
                    {
                        "symbol": symbol,
                        "side": "sell",
                        "qty": current_qty,
                        "price": price,
                        "est_value": current_qty * price,
                    }
                )
            elif delta_val < Decimal("-5.00"):
                # Partial reduction
                sell_val = abs(delta_val)
                sell_qty = (sell_val / price).quantize(Decimal("0.0001"))
                sell_qty = min(sell_qty, current_qty)

                if sell_qty > Decimal("0"):
                    sells.append(
                        {
                            "symbol": symbol,
                            "side": "sell",
                            "qty": sell_qty,
                            "price": price,
                            "est_value": sell_qty * price,
                        }
                    )

            elif delta_val > Decimal("5.00"):
                # BUY: add to position
                buy_qty = (delta_val / price).quantize(Decimal("0.0001"))
                if buy_qty > Decimal("0"):
                    buys.append(
                        {
                            "symbol": symbol,
                            "side": "buy",
                            "qty": buy_qty,
                            "price": price,
                            "est_value": buy_qty * price,
                        }
                    )

        # 1. Execute SELLS first to free cash
        for order_spec in sells:
            try:
                order_res = await self._place_order(
                    symbol=order_spec["symbol"],
                    qty=order_spec["qty"],
                    side="sell",
                    price=order_spec["price"],
                )
                executed_orders.append(order_res)
            except AlpacaBrokerError as err:
                logger.error("Failed to execute sell for %s: %s", order_spec["symbol"], err)

        # 2. Execute BUYS second
        for order_spec in buys:
            try:
                order_res = await self._place_order(
                    symbol=order_spec["symbol"],
                    qty=order_spec["qty"],
                    side="buy",
                    price=order_spec["price"],
                )
                executed_orders.append(order_res)
            except AlpacaBrokerError as err:
                logger.error("Failed to execute buy for %s: %s", order_spec["symbol"], err)

        logger.info(
            "Rebalance completed: %d orders submitted (%d sells, %d buys).",
            len(executed_orders),
            len(sells),
            len(buys),
        )
        return executed_orders

    async def _place_order(
        self,
        symbol: str,
        qty: Decimal,
        side: str,
        price: Decimal,
    ) -> dict[str, Any]:
        """Submit a single market order or execute in mock paper mode."""
        if not self.is_connected_mode:
            order_id = str(uuid.uuid4())
            trade_value = qty * price

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

            order_record = {
                "id": order_id,
                "symbol": symbol,
                "qty": str(qty),
                "side": side,
                "type": "market",
                "filled_avg_price": str(price),
                "status": "filled",
                "created_at": dt.datetime.now(dt.UTC).isoformat(),
                "mode": "simulated_paper",
            }
            self._mock_orders.append(order_record)
            return order_record

        # Connected live/paper Alpaca API call
        async with httpx.AsyncClient(timeout=10) as client:
            payload = {
                "symbol": symbol,
                "qty": str(qty),
                "side": side,
                "type": "market",
                "time_in_force": "day",
            }
            resp = await client.post(
                f"{self.base_url}/v2/orders",
                headers=self._get_headers(),
                json=payload,
            )
            if resp.status_code not in (200, 201):
                raise AlpacaBrokerError(
                    f"Alpaca order placement failed for {symbol} {side} {qty}: "
                    f"{resp.status_code} - {resp.text}"
                )
            res_json = resp.json()
            return {
                "id": str(res_json.get("id")),
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
