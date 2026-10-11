"""Tests for MT5Broker execution gateway.

Verifies:
1. Orders without attached Stop Loss and Take Profit are refused.
2. Inverted protection geometry (SL on wrong side of price, TP on wrong side) is refused.
3. Quantities must be positive.
4. Paper mode order placement returns correct MT5OrderResult with exact Decimal fields.
5. Position queries translate MT5 data into clean stringified Decimals.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from engine.execution.mt5_broker import MT5Broker, MT5BrokerError, OrderSide


@pytest.fixture(autouse=True)
def _mock_mt5_init() -> Iterator[None]:
    with patch("MetaTrader5.initialize", return_value=True):
        yield


def test_market_order_requires_sl_and_tp() -> None:
    broker = MT5Broker(paper_mode=True)

    with pytest.raises(MT5BrokerError, match="protection"):
        broker.place_market_order(
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            stop_loss=None,  # type: ignore[arg-type]
            take_profit=Decimal("2750.00"),
        )

    with pytest.raises(MT5BrokerError, match="protection"):
        broker.place_market_order(
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            stop_loss=Decimal("2690.00"),
            take_profit=None,  # type: ignore[arg-type]
        )


def test_market_order_requires_positive_protection_and_quantity() -> None:
    broker = MT5Broker(paper_mode=True)

    with pytest.raises(MT5BrokerError, match="strictly positive"):
        broker.place_market_order(
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            stop_loss=Decimal("0"),
            take_profit=Decimal("2750.00"),
        )

    with pytest.raises(MT5BrokerError, match="strictly positive"):
        broker.place_market_order(
            side=OrderSide.BUY,
            quantity=Decimal("0"),
            stop_loss=Decimal("2690.00"),
            take_profit=Decimal("2750.00"),
        )


def test_market_order_inverted_geometry_is_refused() -> None:
    broker = MT5Broker(paper_mode=True)

    # Mock symbol tick at 2700.00 bid / 2700.50 ask
    mock_tick = MagicMock()
    mock_tick.bid = 2700.00
    mock_tick.ask = 2700.50

    with patch("MetaTrader5.symbol_info_tick", return_value=mock_tick):
        # BUY price is 2700.50: SL must be < 2700.50, TP must be > 2700.50
        with pytest.raises(MT5BrokerError, match="Invalid BUY protection"):
            broker.place_market_order(
                side=OrderSide.BUY,
                quantity=Decimal("0.01"),
                stop_loss=Decimal("2710.00"),  # SL above price
                take_profit=Decimal("2750.00"),
            )

        with pytest.raises(MT5BrokerError, match="Invalid BUY protection"):
            broker.place_market_order(
                side=OrderSide.BUY,
                quantity=Decimal("0.01"),
                stop_loss=Decimal("2690.00"),
                take_profit=Decimal("2695.00"),  # TP below price
            )

        # SELL price is 2700.00: SL must be > 2700.00, TP must be < 2700.00
        with pytest.raises(MT5BrokerError, match="Invalid SELL protection"):
            broker.place_market_order(
                side=OrderSide.SELL,
                quantity=Decimal("0.01"),
                stop_loss=Decimal("2690.00"),  # SL below price
                take_profit=Decimal("2650.00"),
            )


def test_limit_order_requires_sl_and_tp() -> None:
    broker = MT5Broker(paper_mode=True)

    with pytest.raises(MT5BrokerError, match="protection"):
        broker.place_limit_order(
            side=OrderSide.BUY,
            limit_price=Decimal("2690.00"),
            quantity=Decimal("0.01"),
            stop_loss=None,  # type: ignore[arg-type]
            take_profit=Decimal("2750.00"),
        )

    # Inverted limit buy: SL must be below limit, TP above limit
    with pytest.raises(MT5BrokerError, match="Invalid BUY LIMIT protection"):
        broker.place_limit_order(
            side=OrderSide.BUY,
            limit_price=Decimal("2690.00"),
            quantity=Decimal("0.01"),
            stop_loss=Decimal("2695.00"),
            take_profit=Decimal("2750.00"),
        )


def test_paper_mode_market_order_execution() -> None:
    broker = MT5Broker(symbol="XAUUSDm", paper_mode=True)

    mock_tick = MagicMock()
    mock_tick.bid = 2700.00
    mock_tick.ask = 2700.50

    with patch("MetaTrader5.symbol_info_tick", return_value=mock_tick):
        res = broker.place_market_order(
            side=OrderSide.BUY,
            quantity=Decimal("0.02"),
            stop_loss=Decimal("2695.00"),
            take_profit=Decimal("2720.00"),
            comment="Test Trade",
        )

        assert res.success is True
        assert res.volume == Decimal("0.02")
        assert res.price == Decimal("2700.500")
        assert res.order_id == 999999


def test_paper_mode_sell_order_execution() -> None:
    broker = MT5Broker(symbol="XAUUSDm", paper_mode=True)

    mock_tick = MagicMock()
    mock_tick.bid = 2700.00
    mock_tick.ask = 2700.50

    with patch("MetaTrader5.symbol_info_tick", return_value=mock_tick):
        # Valid SELL: SL is above bid price (2705 > 2700), TP is below bid price (2680 < 2700)
        res = broker.place_market_order(
            side=OrderSide.SELL,
            quantity=Decimal("0.05"),
            stop_loss=Decimal("2705.00"),
            take_profit=Decimal("2680.00"),
            comment="Valid Short",
        )

        assert res.success is True
        assert res.volume == Decimal("0.05")
        assert res.price == Decimal("2700.000")
        assert res.order_id == 999999


def test_valid_live_market_order_is_sent_to_mt5() -> None:
    import MetaTrader5 as mt5  # noqa: N813

    broker = MT5Broker(symbol="XAUUSDm", paper_mode=False)

    mock_tick = MagicMock()
    mock_tick.bid = 2700.00
    mock_tick.ask = 2700.50

    mock_result = MagicMock()
    mock_result.retcode = mt5.TRADE_RETCODE_DONE
    mock_result.order = 88881
    mock_result.deal = 99991
    mock_result.volume = 0.02
    mock_result.price = 2700.50
    mock_result.comment = "Done"

    mock_info = MagicMock()
    mock_info.filling_mode = 2  # IOC

    with (
        patch("MetaTrader5.symbol_info_tick", return_value=mock_tick),
        patch("MetaTrader5.symbol_info", return_value=mock_info),
        patch("MetaTrader5.order_send", return_value=mock_result) as mock_send,
    ):
        res = broker.place_market_order(
            side=OrderSide.BUY,
            quantity=Decimal("0.02"),
            stop_loss=Decimal("2690.00"),
            take_profit=Decimal("2720.00"),
            comment="Live Judas Buy",
        )

        assert res.success is True
        assert res.order_id == 88881
        assert res.deal_id == 99991
        assert res.volume == Decimal("0.02")

        # Verify the actual request sent to MT5
        mock_send.assert_called_once()
        req = mock_send.call_args[0][0]
        assert req["symbol"] == "XAUUSDm"
        assert req["type"] == mt5.ORDER_TYPE_BUY
        assert req["sl"] == 2690.0
        assert req["tp"] == 2720.0
        assert req["volume"] == 0.02


def test_get_open_positions_formatting() -> None:
    broker = MT5Broker(paper_mode=True)

    mock_pos = MagicMock()
    mock_pos.ticket = 12345
    mock_pos.time = 1710000000
    mock_pos.type = 0  # ORDER_TYPE_BUY
    mock_pos.volume = 0.05
    mock_pos.price_open = 2700.123
    mock_pos.price_current = 2705.456
    mock_pos.sl = 2690.000
    mock_pos.tp = 2720.000
    mock_pos.profit = 26.65
    mock_pos.comment = "ICT Trade"

    with (
        patch("MetaTrader5.initialize", return_value=True),
        patch("MetaTrader5.positions_get", return_value=[mock_pos]),
    ):
        positions = broker.get_open_positions()
        assert len(positions) == 1
        p = positions[0]
        assert p["ticket"] == 12345
        assert p["side"] == "BUY"
        assert p["volume"] == "0.05"
        assert p["open_price"] == "2700.123"
        assert p["current_price"] == "2705.456"
        assert p["sl"] == "2690.000"
        assert p["tp"] == "2720.000"
        assert p["profit"] == "26.65"


def test_get_closed_deal_for_position() -> None:
    broker = MT5Broker(paper_mode=False)

    mock_deal = MagicMock()
    mock_deal.ticket = 998877
    mock_deal.order = 12345
    mock_deal.position_id = 12345
    mock_deal.entry = 1  # mt5.DEAL_ENTRY_OUT
    mock_deal.price = 2715.500
    mock_deal.profit = 150.00
    mock_deal.commission = -2.50
    mock_deal.swap = 0.00
    mock_deal.time = 1710003600
    mock_deal.comment = "[tp 2715.500]"

    with (
        patch("MetaTrader5.initialize", return_value=True),
        patch("MetaTrader5.history_deals_get", return_value=[mock_deal]),
    ):
        deal_info = broker.get_closed_deal_for_position(12345)
        assert deal_info is not None
        assert deal_info["ticket"] == 998877
        assert deal_info["price"] == Decimal("2715.500")
        assert deal_info["profit"] == Decimal("147.50")
        assert deal_info["comment"] == "[tp 2715.500]"

