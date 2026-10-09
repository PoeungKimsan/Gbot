"""The risk document: what a leg of risk costs, and when the day is over.

``config/risk.yaml`` holds two numbers and nothing else, because those are the only
two the Phase 3 modules read. A third knob would be a placeholder for a policy that
does not exist yet, which is how configuration files accrue dead weight.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Final

from engine.risk.decimal_yaml import decimal_scalar, load_decimal_yaml
from engine.risk.rounding import RoundingError, require_decimal

__all__ = [
    "DEFAULT_RISK_CONFIG_PATH",
    "RiskConfig",
    "RiskConfigError",
    "load_risk_config",
]

#: The committed risk document, relative to the repository root.
DEFAULT_RISK_CONFIG_PATH: Final[Path] = Path("config") / "risk.yaml"

#: A fixed-fractional policy cannot risk more than the account it is risking from.
_MAX_RISK_FRACTION: Final[Decimal] = Decimal("1")


class RiskConfigError(ValueError):
    """The risk document, or a value derived from it, is out of policy."""


@dataclasses.dataclass(frozen=True, slots=True)
class RiskConfig:
    """The risk policy the sizer and the kill-switch share.

    ``risk_per_trade`` is the fraction of equity risked per position, so the amount
    risked is ``equity * risk_per_trade``. ``daily_loss_limit_r`` is the day's loss
    ceiling measured in units of R, where one R is that same risked amount.
    """

    risk_per_trade: Decimal
    daily_loss_limit_r: Decimal

    def __post_init__(self) -> None:
        for field in ("risk_per_trade", "daily_loss_limit_r"):
            try:
                require_decimal(getattr(self, field), field=field, positive=True)
            except RoundingError as exc:
                raise RiskConfigError(str(exc)) from exc

        if self.risk_per_trade > _MAX_RISK_FRACTION:
            raise RiskConfigError(
                f"risk_per_trade must not exceed 1 (risk more than the account), "
                f"got {self.risk_per_trade}"
            )

    def risk_amount(self, equity: Decimal) -> Decimal:
        """The amount risked per trade at ``equity``: one R in currency.

        Raises:
            RiskConfigError: if equity is not a finite, non-negative Decimal.
        """
        try:
            validated = require_decimal(equity, field="equity", non_negative=True)
        except RoundingError as exc:
            raise RiskConfigError(str(exc)) from exc
        return validated * self.risk_per_trade


def load_risk_config(path: Path | str = DEFAULT_RISK_CONFIG_PATH) -> RiskConfig:
    """Load and validate the risk document.

    Args:
        path: Path to ``config/risk.yaml``.

    Returns:
        The parsed :class:`RiskConfig`.

    Raises:
        DecimalYamlError: if the file is unreadable or carries a float.
        RiskConfigError: if a value is out of policy.
    """
    document = load_decimal_yaml(path)
    return RiskConfig(
        risk_per_trade=decimal_scalar(document, "risk_per_trade"),
        daily_loss_limit_r=decimal_scalar(document, "daily_loss_limit_r"),
    )
