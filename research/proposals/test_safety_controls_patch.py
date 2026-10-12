"""Unit tests validating the Safety Controls Patch Proposal.

Verifies:
1. Minimum stop floor: max(broker minimum, 3x spread). Stops below floor are refused and logged.
2. Leverage cap: Notional leverage cannot exceed max_leverage_ratio. Clamps lots or refuses trade.
3. Risk % recomputation: Effective risk is recomputed and logged whenever lots are clamped.
4. Signal rejection: Any rule violation cleanly aborts order placement.
"""

from decimal import Decimal
import logging
from typing import Any
import pytest

from engine.risk.rounding import floor_to_step

class MockRiskConfig:
    def __init__(
        self,
        risk_per_trade: str = "0.01",
        daily_loss_limit_r: str = "3",
        min_stop_distance: str = "0.60",
        max_leverage_ratio: str = "30.0",
        max_lot_size: str = "10.00",
    ) -> None:
        self.risk_per_trade = Decimal(risk_per_trade)
        self.daily_loss_limit_r = Decimal(daily_loss_limit_r)
        self.min_stop_distance = Decimal(min_stop_distance)
        self.max_leverage_ratio = Decimal(max_leverage_ratio)
        self.max_lot_size = Decimal(max_lot_size)

    def risk_amount(self, equity: Decimal) -> Decimal:
        return equity * self.risk_per_trade


class ProposedSizer:
    """Implements the logic proposed in safety_controls_patch.diff."""

    def __init__(self, risk_config: MockRiskConfig, logger: logging.Logger) -> None:
        self.risk_config = risk_config
        self.logger = logger

    def calculate_lot_size(
        self,
        entry: Decimal,
        stop_loss: Decimal,
        equity: Decimal,
        spread: Decimal = Decimal("0.20"),
    ) -> tuple[Decimal, dict[str, Any]]:
        stop_distance = abs(entry - stop_loss)

        # 1. Minimum stop floor: max(broker_min, 3x spread, config.min_stop_distance)
        broker_min_stop = Decimal("0.20")
        min_stop_floor = max(broker_min_stop, Decimal("3") * spread, self.risk_config.min_stop_distance)
        if stop_distance < min_stop_floor:
            self.logger.warning(
                "Order REFUSED: stop distance %s is below minimum realism floor %s (max(broker_min, 3x spread)); signal rejected",
                stop_distance,
                min_stop_floor,
            )
            return Decimal("0"), {
                "status": "REJECTED",
                "reason": f"stop_distance {stop_distance} < min_floor {min_stop_floor}",
            }

        contract_size = Decimal("100")
        risk_amount = self.risk_config.risk_amount(equity)
        dollar_risk_per_lot = stop_distance * contract_size
        if dollar_risk_per_lot <= Decimal("0"):
            return Decimal("0"), {"status": "REJECTED", "reason": "invalid_dollar_risk"}

        raw_lots = floor_to_step(risk_amount / dollar_risk_per_lot, precision=2)
        if raw_lots < Decimal("0.01"):
            self.logger.warning(
                "Dynamic lot size %s below venue minimum 0.01 (risk=$%s, stop_dist=$%s); signal rejected",
                raw_lots,
                risk_amount,
                stop_distance,
            )
            return Decimal("0"), {"status": "REJECTED", "reason": "below_min_lots"}

        # 2. Leverage cap by equity
        max_notional = equity * self.risk_config.max_leverage_ratio
        max_lots_by_leverage = floor_to_step(max_notional / (contract_size * entry), precision=2)
        if raw_lots > max_lots_by_leverage:
            actual_leverage = (raw_lots * contract_size * entry) / equity
            self.logger.warning(
                "Leverage cap triggered: raw lots %s implies leverage %sx exceeding cap %sx; clamping to %s lots",
                raw_lots,
                actual_leverage,
                self.risk_config.max_leverage_ratio,
                max_lots_by_leverage,
            )

        clamped_lots = min(raw_lots, max_lots_by_leverage, self.risk_config.max_lot_size)

        # 3. Recompute effective risk % after clamping
        effective_dollar_risk = clamped_lots * dollar_risk_per_lot
        effective_risk_pct = (effective_dollar_risk / equity) * Decimal("100")
        if clamped_lots < raw_lots:
            self.logger.warning(
                "Dynamic lot size clamped: raw %s lots -> %s lots. Effective risk recomputed: %s%% (target %s%%)",
                raw_lots,
                clamped_lots,
                effective_risk_pct,
                self.risk_config.risk_per_trade * Decimal("100"),
            )

        if clamped_lots < Decimal("0.01"):
            self.logger.warning("Order REFUSED: clamped lot size %s below minimum 0.01", clamped_lots)
            return Decimal("0"), {"status": "REJECTED", "reason": "clamped_below_min"}

        notional_usd = clamped_lots * contract_size * entry
        final_leverage = notional_usd / equity

        return clamped_lots, {
            "status": "ACCEPTED",
            "raw_lots": raw_lots,
            "clamped_lots": clamped_lots,
            "effective_risk_pct": effective_risk_pct,
            "effective_dollar_risk": effective_dollar_risk,
            "notional_usd": notional_usd,
            "leverage": final_leverage,
        }


@pytest.fixture
def sizer(caplog):
    caplog.set_level(logging.WARNING)
    logger = logging.getLogger("test_safety_controls")
    cfg = MockRiskConfig()
    return ProposedSizer(cfg, logger)


def test_reject_stop_below_minimum_floor(sizer, caplog):
    """Sub-floor stops (< $0.60) must be rejected and logged."""
    lots, meta = sizer.calculate_lot_size(
        entry=Decimal("2000.00"),
        stop_loss=Decimal("1999.79"), # stop_distance = 0.21
        equity=Decimal("10000.00"),
        spread=Decimal("0.20"),
    )
    assert lots == Decimal("0")
    assert meta["status"] == "REJECTED"
    assert "below minimum realism floor" in caplog.text


def test_accept_stop_at_or_above_minimum_floor(sizer, caplog):
    """Stops at or above $0.60 must be accepted."""
    lots, meta = sizer.calculate_lot_size(
        entry=Decimal("2000.00"),
        stop_loss=Decimal("1999.00"), # stop_distance = 1.00
        equity=Decimal("10000.00"),
        spread=Decimal("0.20"),
    )
    assert lots == Decimal("1.00")
    assert meta["status"] == "ACCEPTED"
    assert meta["effective_risk_pct"] == Decimal("1.00")


def test_leverage_cap_clamps_oversized_notional(sizer, caplog):
    """When stop is $0.60 but gold is at $4,100, raw leverage is 68x, cap must clamp to 30x."""
    lots, meta = sizer.calculate_lot_size(
        entry=Decimal("4100.00"),
        stop_loss=Decimal("4099.40"), # stop_distance = 0.60
        equity=Decimal("10000.00"),
        spread=Decimal("0.20"),
    )
    # Raw lots = 100 / (0.60 * 100) = 1.66 lots -> 1.66 * 100 * 4100 = $680,600 (68x lev)
    # Cap 30x: 300,000 / (100 * 4100) = 0.73 lots
    assert lots == Decimal("0.73")
    assert meta["status"] == "ACCEPTED"
    assert meta["leverage"] <= Decimal("30.0")
    assert "Leverage cap triggered" in caplog.text
    # Effective risk is recomputed to ~0.438%
    assert meta["effective_risk_pct"] == Decimal("43.80") / Decimal("100")
    assert "Effective risk recomputed" in caplog.text


def test_recompute_risk_when_10_lot_clamp_binds(sizer, caplog):
    """When $100k account with $0.60 stop binds at 10.00 lots, risk recomputes to 0.60%."""
    lots, meta = sizer.calculate_lot_size(
        entry=Decimal("2000.00"),
        stop_loss=Decimal("1999.40"), # stop_distance = 0.60
        equity=Decimal("100000.00"),
        spread=Decimal("0.20"),
    )
    # Raw lots = 1000 / (0.60 * 100) = 16.66 lots
    # Clamped to 10.00 lots
    assert lots == Decimal("10.00")
    assert meta["effective_risk_pct"] == Decimal("0.60")
    assert "Dynamic lot size clamped" in caplog.text


def test_reject_below_minimum_lot_size(sizer, caplog):
    """Account too small for 0.01 lot must be rejected and logged."""
    lots, meta = sizer.calculate_lot_size(
        entry=Decimal("2000.00"),
        stop_loss=Decimal("1995.00"), # stop_distance = 5.00
        equity=Decimal("100.00"),     # risk budget = $1.00 -> raw lots = 1 / 500 = 0.002
        spread=Decimal("0.20"),
    )
    assert lots == Decimal("0")
    assert meta["status"] == "REJECTED"
    assert "below venue minimum 0.01" in caplog.text
