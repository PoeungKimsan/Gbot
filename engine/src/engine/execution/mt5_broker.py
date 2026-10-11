"""Order execution gateway for MetaTrader 5.

Translates domain trade intents into MqlTradeRequest calls sent to the active
MetaTrader 5 terminal, with real broker-enforced Stop Loss and Take Profit
parameters, volume rounding, and position monitoring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

import MetaTrader5 as mt5

logger = logging.getLogger(__name__)

__all__ = [
    "MT5Broker",
    "MT5BrokerError",
    "MT5OrderResult",
    "OrderSide",
]


class MT5BrokerError(RuntimeError):
    """Raised when an order request fails or is rejected by the broker."""


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class MT5OrderResult:
    retcode: int
    deal_id: int
    order_id: int
    volume: Decimal
    price: Decimal
    comment: str
    success: bool
    error_message: str = ""


class MT5Broker:
    """Executes live orders on MetaTrader 5 with Stop Loss and Take Profit."""

    def __init__(
        self,
        symbol: str = "XAUUSDm",
        magic_number: int = 240811,
        paper_mode: bool = False,
    ) -> None:
        self.symbol = symbol
        self.magic_number = magic_number
        self.paper_mode = paper_mode
        self._initialized = False

    def connect(self) -> None:
        if not mt5.initialize():
            err = mt5.last_error()
            raise MT5BrokerError(f"MT5 initialization failed: {err}")
        self._initialized = True

    def _ensure_connected(self) -> None:
        if not self._initialized:
            self.connect()

    def _determine_filling_type(self) -> int:
        info = mt5.symbol_info(self.symbol)
        if info is None:
            return mt5.ORDER_FILLING_IOC

        # Bitmask for filling modes
        # 1 = FOK, 2 = IOC
        filling_mode = info.filling_mode
        if filling_mode & 2 != 0:
            return mt5.ORDER_FILLING_IOC
        if filling_mode & 1 != 0:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def place_market_order(
        self,
        side: OrderSide,
        quantity: Decimal,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        comment: str = "ICT Trade",
    ) -> MT5OrderResult:
        """Execute a market order with attached SL and TP."""
        self._ensure_connected()

        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            raise MT5BrokerError(f"Cannot get tick for {self.symbol}")

        price = tick.ask if side == OrderSide.BUY else tick.bid
        order_type = mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL

        if self.paper_mode:
            logger.info(
                "[PAPER] Market order: %s %s @ %s, SL=%s, TP=%s",
                side.value,
                quantity,
                price,
                stop_loss,
                take_profit,
            )
            return MT5OrderResult(
                retcode=10009,
                deal_id=999999,
                order_id=999999,
                volume=quantity,
                price=Decimal(f"{price:.3f}"),
                comment=f"[PAPER] {comment}",
                success=True,
            )

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": float(quantity),
            "type": order_type,
            "price": float(price),
            "deviation": 20,
            "magic": self.magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._determine_filling_type(),
        }

        if stop_loss is not None:
            request["sl"] = float(stop_loss)
        if take_profit is not None:
            request["tp"] = float(take_profit)

        result = mt5.order_send(request)
        if result is None:
            err = mt5.last_error()
            raise MT5BrokerError(f"order_send failed: {err}")

        success = result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
        error_msg = "" if success else f"Retcode {result.retcode}: {result.comment}"

        logger.info(
            "MT5 Order result: success=%s, order=%s, deal=%s, retcode=%s (%s)",
            success,
            result.order,
            result.deal,
            result.retcode,
            result.comment,
        )

        return MT5OrderResult(
            retcode=result.retcode,
            deal_id=result.deal,
            order_id=result.order,
            volume=Decimal(str(result.volume)) if result.volume else quantity,
            price=Decimal(f"{result.price:.3f}") if result.price else Decimal(f"{price:.3f}"),
            comment=result.comment,
            success=success,
            error_message=error_msg,
        )

    def place_limit_order(
        self,
        side: OrderSide,
        limit_price: Decimal,
        quantity: Decimal,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        comment: str = "ICT Limit",
    ) -> MT5OrderResult:
        """Place a resting limit order in MT5 with SL and TP."""
        self._ensure_connected()

        order_type = mt5.ORDER_TYPE_BUY_LIMIT if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL_LIMIT

        if self.paper_mode:
            logger.info(
                "[PAPER] Limit order: %s %s @ %s, SL=%s, TP=%s",
                side.value,
                quantity,
                limit_price,
                stop_loss,
                take_profit,
            )
            return MT5OrderResult(
                retcode=10009,
                deal_id=999999,
                order_id=999999,
                volume=quantity,
                price=limit_price,
                comment=f"[PAPER] {comment}",
                success=True,
            )

        request: dict[str, Any] = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.symbol,
            "volume": float(quantity),
            "type": order_type,
            "price": float(limit_price),
            "deviation": 20,
            "magic": self.magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._determine_filling_type(),
        }

        if stop_loss is not None:
            request["sl"] = float(stop_loss)
        if take_profit is not None:
            request["tp"] = float(take_profit)

        result = mt5.order_send(request)
        if result is None:
            err = mt5.last_error()
            raise MT5BrokerError(f"order_send failed: {err}")

        success = result.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED)
        error_msg = "" if success else f"Retcode {result.retcode}: {result.comment}"

        return MT5OrderResult(
            retcode=result.retcode,
            deal_id=result.deal,
            order_id=result.order,
            volume=Decimal(str(result.volume)) if result.volume else quantity,
            price=limit_price,
            comment=result.comment,
            success=success,
            error_message=error_msg,
        )

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Return all open positions for this symbol."""
        self._ensure_connected()
        positions = mt5.positions_get(symbol=self.symbol)
        if positions is None:
            return []

        out: list[dict[str, Any]] = []
        for p in positions:
            side = "BUY" if p.type == mt5.ORDER_TYPE_BUY else "SELL"
            out.append(
                {
                    "ticket": p.ticket,
                    "time": dt.datetime.fromtimestamp(p.time, dt.UTC).isoformat(),
                    "side": side,
                    "volume": str(p.volume),
                    "open_price": f"{p.price_open:.3f}",
                    "current_price": f"{p.price_current:.3f}",
                    "sl": f"{p.sl:.3f}" if p.sl else None,
                    "tp": f"{p.tp:.3f}" if p.tp else None,
                    "profit": f"{p.profit:.2f}",
                    "comment": p.comment,
                }
            )
        return out

    def close_position(self, ticket: int) -> bool:
        """Close an open position by ticket number."""
        self._ensure_connected()
        pos = mt5.positions_get(ticket=ticket)
        if not pos:
            logger.warning("Position ticket %s not found to close", ticket)
            return False

        position = pos[0]
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return False

        opposite_type = mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = tick.bid if position.type == mt5.ORDER_TYPE_BUY else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket,
            "symbol": self.symbol,
            "volume": position.volume,
            "type": opposite_type,
            "price": float(price),
            "deviation": 20,
            "magic": self.magic_number,
            "comment": "Close position",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._determine_filling_type(),
        }

        res = mt5.order_send(request)
        return res is not None and res.retcode == mt5.TRADE_RETCODE_DONE
