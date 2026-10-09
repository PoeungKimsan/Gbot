"""Fixed-fractional position sizing.

The rule is one line: risk ``equity * risk_per_trade``, and size the position so a
stop-out costs at most that much. The consequences are what this module is careful
about:

* **Floor, never round.** ``risk_amount / stop_distance`` is floored to the venue's
  ``trade_units_precision``. Rounding to nearest could produce a position whose
  stop-out costs *more* than the amount risked, which is the one direction a risk
  rule may never err in (AGENTS.md 2.5).
* **A small position is refused, not enlarged.** If the floored quantity is below
  ``minimum_trade_size`` the order does not exist. Scaling up to reach the minimum
  would silently break the budget the whole rule rests on.
* **Geometry comes from the venue.** Precision and minimum size are read from
  :class:`~engine.market.instrument.InstrumentSpec`, which itself comes from OANDA.
  Nothing here invents a tick, a contract multiplier, or a lot size. For XAU_USD one
  unit is one troy ounce, so ``quantity * price`` is the notional - the same
  convention :mod:`engine.domain.positions` uses, and the one the sizing result is
  expressed in.

``BelowMinimumTradeSize`` is deliberately not named with an ``Error`` suffix: it is a
*condition* (a small account, or a wide stop) rather than a defect in the caller's
input, and the distinction is load-bearing - one is journalled as a declined
opportunity, the other raised as a bug. The rule is suppressed for this package in
``pyproject.toml`` with that reasoning recorded there, exactly as it is for
``JournalExhausted`` in ``engine/journal``.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Final

from engine.market.instrument import InstrumentSpec
from engine.risk.rounding import RoundingError, floor_to_step, require_decimal

__all__ = [
    "BelowMinimumTradeSize",
    "PositionSize",
    "SizingError",
    "r_multiple",
    "size_position",
]

#: A fixed-fractional policy cannot risk more than the account it risks from.
_MAX_RISK_FRACTION: Final[Decimal] = Decimal("1")


class SizingError(ValueError):
    """A position cannot be sized within policy, or an input is unusable."""


class BelowMinimumTradeSize(SizingError):
    """The sized position is below the venue's minimum, so the order is refused.

    A distinct type because the caller's response is different: an out-of-policy
    input is a bug, while a below-minimum size is a legitimate outcome of a small
    account or a wide stop, and should be journalled rather than raised at an
    operator.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class PositionSize:
    """A sized position and the budget it was sized against.

    ``units`` is expressed at the venue's ``trade_units_precision``, so it can be
    sent onward as a quantity without further rounding.
    """

    equity: Decimal
    risk_per_trade: Decimal
    risk_amount: Decimal
    stop_distance: Decimal
    units: Decimal
    spec: InstrumentSpec

    @property
    def worst_case_loss(self) -> Decimal:
        """What a stop-out costs: the stop distance times the units held.

        Always at most :attr:`risk_amount`, because the units were floored.
        """
        return self.stop_distance * self.units

    @property
    def risk_used(self) -> Decimal:
        """The fraction of the risked amount actually committed, as a Decimal."""
        if self.risk_amount == 0:  # pragma: no cover - guarded by size_position
            return Decimal(0)
        return self.worst_case_loss / self.risk_amount

    def notional_at(self, price: Decimal) -> Decimal:
        """Units times ``price``, in quote currency.

        Args:
            price: A positive, finite price.

        Raises:
            SizingError: if the price is not a usable Decimal.
        """
        try:
            validated = require_decimal(price, field="price", positive=True)
        except RoundingError as exc:
            raise SizingError(str(exc)) from exc
        return self.units * validated


def size_position(
    *,
    equity: Decimal,
    stop_distance: Decimal,
    risk_per_trade: Decimal,
    spec: InstrumentSpec,
) -> PositionSize:
    """Size a position by risk budget.

    Args:
        equity: Current account equity.
        stop_distance: Distance from entry to stop, in price units. Positive.
        risk_per_trade: Fraction of equity to risk, in ``(0, 1]``.
        spec: The venue's instrument geometry.

    Returns:
        The :class:`PositionSize`.

    Raises:
        SizingError: if any input is unusable, or the risk fraction is above one.
        BelowMinimumTradeSize: if the sized quantity is below ``spec``'s minimum.
    """
    values = (
        ("equity", equity, True),
        ("stop_distance", stop_distance, True),
        ("risk_per_trade", risk_per_trade, False),
    )
    for field, value, positive in values:
        try:
            require_decimal(value, field=field, positive=positive)
        except RoundingError as exc:
            raise SizingError(str(exc)) from exc

    if risk_per_trade > _MAX_RISK_FRACTION:
        raise SizingError(
            f"risk_per_trade must not exceed 1 (risk more than the account), "
            f"got {risk_per_trade}"
        )
    if not isinstance(spec, InstrumentSpec):
        raise SizingError(f"spec must be an InstrumentSpec, got {type(spec).__name__}")

    risk_amount = equity * risk_per_trade
    try:
        units = floor_to_step(
            risk_amount / stop_distance, precision=spec.trade_units_precision
        )
    except RoundingError as exc:
        raise SizingError(str(exc)) from exc

    if units < spec.minimum_trade_size:
        raise BelowMinimumTradeSize(
            f"sizing {equity} at {risk_per_trade} over a stop of {stop_distance} "
            f"gives {units} units, below the {spec.minimum_trade_size} minimum for "
            f"{spec.name}"
        )

    return PositionSize(
        equity=equity,
        risk_per_trade=risk_per_trade,
        risk_amount=risk_amount,
        stop_distance=stop_distance,
        units=units,
        spec=spec,
    )


def r_multiple(pnl: Decimal, risk_amount: Decimal) -> Decimal:
    """Express a realised PnL as a multiple of the amount risked.

    Args:
        pnl: Realised profit or loss, in currency.
        risk_amount: The amount that was risked, strictly positive.

    Returns:
        The R multiple: ``-0.5`` means half a loss, ``2`` means two R of profit.

    Raises:
        SizingError: if either input is unusable, or ``risk_amount`` is not
            strictly positive.
    """
    try:
        realised = require_decimal(pnl, field="pnl")
        risked = require_decimal(risk_amount, field="risk_amount", positive=True)
    except RoundingError as exc:
        raise SizingError(str(exc)) from exc
    return realised / risked
