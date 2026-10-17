"""Telegram notification service for Gbot execution alerts.

Sends non-blocking real-time alerts for:
- New order executions (side, volume, entry, SL, TP)
- Position closes (audited net PnL, exit price, daily cumulative PnL)
- Daily loss kill-switch activations
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from decimal import Decimal

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

__all__ = ["TelegramNotifier"]


class TelegramNotifier:
    """Dispatches asynchronous alerts to a Telegram chat or channel."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        if not os.getenv("PYTEST_CURRENT_TEST"):
            load_dotenv()
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self._enabled = bool(self.bot_token and self.chat_id)

        if self._enabled:
            logger.info("TelegramNotifier initialized for chat ID: %s", self.chat_id)
        else:
            logger.info("TelegramNotifier disabled (no TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    async def send_message(self, text: str) -> bool:
        """Send a plain or Markdown message via Telegram Bot API."""
        if not self._enabled or not self.bot_token or not self.chat_id:
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "Telegram API error (status %d): %s",
                    resp.status_code,
                    resp.text,
                )
                return False
        except Exception as exc:
            logger.warning("Failed to send Telegram alert: %s", exc)
            return False

    async def notify_order_opened(
        self,
        order_id: int | str,
        side: str,
        volume: Decimal | str,
        entry_price: Decimal | str,
        stop_loss: Decimal | str,
        take_profit: Decimal | str,
        symbol: str = "ETF",
        strategy: str = "VAR_117 ETF Momentum",
        reason: str = "",
    ) -> bool:
        """Send notification when a new trade is executed."""
        emoji = "🟢" if str(side).upper() == "BUY" else "🔴"
        now_utc = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        msg = (
            f"<b>{emoji} NEW POSITION OPENED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Ticket:</b> #{order_id}\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Side:</b> {str(side).upper()}\n"
            f"<b>Volume:</b> {volume} Lots\n"
            f"<b>Entry Price:</b> ${entry_price}\n"
            f"<b>Stop Loss:</b> ${stop_loss}\n"
            f"<b>Take Profit:</b> ${take_profit}\n"
            f"<b>Strategy:</b> {strategy}\n"
        )
        if reason:
            msg += f"<b>Reason:</b> {reason}\n"
        msg += f"<b>Time:</b> {now_utc}"

        return await self.send_message(msg)

    async def notify_order_closed(
        self,
        ticket: int | str,
        symbol: str,
        exit_price: Decimal | str,
        profit: Decimal | str,
        daily_pnl: Decimal | str,
        close_time: dt.datetime | None = None,
    ) -> bool:
        """Send notification when an open position closes."""
        pnl_num = Decimal(str(profit))
        emoji = "💰" if pnl_num >= 0 else "🛑"
        sign = "+" if pnl_num >= 0 else ""
        time_str = (
            close_time.strftime("%Y-%m-%d %H:%M:%S UTC")
            if close_time
            else dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        )

        msg = (
            f"<b>{emoji} POSITION CLOSED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Ticket:</b> #{ticket}\n"
            f"<b>Symbol:</b> {symbol}\n"
            f"<b>Exit Price:</b> ${exit_price}\n"
            f"<b>Realized PnL:</b> {sign}${pnl_num:,.2f}\n"
            f"<b>Daily Cumulative PnL:</b> ${Decimal(str(daily_pnl)):,.2f}\n"
            f"<b>Closed At:</b> {time_str}"
        )

        return await self.send_message(msg)

    async def notify_killswitch_tripped(
        self,
        reason: str,
        loss_r: Decimal | str,
        limit_r: Decimal | str,
    ) -> bool:
        """Send notification when the daily loss limit trips the kill-switch."""
        now_utc = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        msg = (
            f"<b>🚨 KILL-SWITCH TRIPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Daily Loss:</b> -{loss_r}R (Limit: -{limit_r}R)\n"
            f"<b>Reason:</b> {reason}\n"
            f"<b>Status:</b> All new entries BLOCKED until 17:00 NY rollover.\n"
            f"<b>Time:</b> {now_utc}"
        )

        return await self.send_message(msg)
