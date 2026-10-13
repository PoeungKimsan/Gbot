"""Tests for TelegramNotifier and LiveTrader Telegram integration."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.runtime.telegram import TelegramNotifier


@pytest.mark.asyncio
async def test_telegram_notifier_disabled_without_credentials() -> None:
    with (
        patch("engine.runtime.telegram.load_dotenv"),
        patch.dict("os.environ", {}, clear=True),
    ):
        notifier = TelegramNotifier(bot_token="", chat_id="")
        assert notifier.is_enabled is False
        assert await notifier.send_message("Test") is False


@pytest.mark.asyncio
async def test_telegram_notifier_send_message_success() -> None:
    notifier = TelegramNotifier(bot_token="fake_token", chat_id="123456789")
    assert notifier.is_enabled is True

    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        success = await notifier.send_message("Hello from test")

        assert success is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "https://api.telegram.org/botfake_token/sendMessage"
        assert call_args[1]["json"]["chat_id"] == "123456789"
        assert call_args[1]["json"]["text"] == "Hello from test"


@pytest.mark.asyncio
async def test_telegram_notify_order_opened() -> None:
    notifier = TelegramNotifier(bot_token="fake_token", chat_id="123456789")

    with patch.object(notifier, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        res = await notifier.notify_order_opened(
            order_id=999123,
            side="BUY",
            volume=Decimal("0.02"),
            entry_price=Decimal("2700.50"),
            stop_loss=Decimal("2690.00"),
            take_profit=Decimal("2725.00"),
            symbol="XAUUSDm",
            strategy="ICT Judas",
            reason="Reclaim Asian Low",
        )

        assert res is True
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "NEW POSITION OPENED" in msg
        assert "#999123" in msg
        assert "BUY" in msg
        assert "$2700.50" in msg
        assert "$2690.00" in msg
        assert "$2725.00" in msg


@pytest.mark.asyncio
async def test_telegram_notify_order_closed() -> None:
    notifier = TelegramNotifier(bot_token="fake_token", chat_id="123456789")

    with patch.object(notifier, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        res = await notifier.notify_order_closed(
            ticket=999123,
            symbol="XAUUSDm",
            exit_price=Decimal("2725.00"),
            profit=Decimal("49.00"),
            daily_pnl=Decimal("49.00"),
            close_time=dt.datetime(2026, 3, 11, 12, 0, tzinfo=dt.UTC),
        )

        assert res is True
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "POSITION CLOSED" in msg
        assert "+$49.00" in msg
        assert "$2725.00" in msg


@pytest.mark.asyncio
async def test_telegram_notify_killswitch_tripped() -> None:
    notifier = TelegramNotifier(bot_token="fake_token", chat_id="123456789")

    with patch.object(notifier, "send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = True

        res = await notifier.notify_killswitch_tripped(
            reason="Daily loss threshold hit",
            loss_r=Decimal("3.00"),
            limit_r=Decimal("3"),
        )

        assert res is True
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "KILL-SWITCH TRIPPED" in msg
        assert "-3.00R" in msg
