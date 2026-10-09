"""Shared Decimal helpers for the risk package.

AGENTS.md 2.1 asks for rounding to be centralized rather than scattered as ad-hoc
``.quantize()`` calls, and for input validation to reject a float before it reaches
arithmetic. Both live here so every risk module refuses a value the same way and
rounds it the same way.
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from typing import Final

__all__ = [
    "RoundingError",
    "floor_to_step",
    "require_decimal",
]

#: The rounding mode every risk quantity uses. Down: a position may be smaller
#: than the ideal and never larger, so a stop-out can never cost more than the
#: amount that was risked.
_ROUNDING: Final[str] = ROUND_FLOOR


class RoundingError(ValueError):
    """A value is not a usable Decimal, or cannot be expressed at a step."""


def require_decimal(
    value: object,
    *,
    field: str,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    """Return ``value`` as a Decimal, or explain why it cannot be used.

    A ``bool`` is rejected explicitly: it is an ``int`` subclass, and accepting it
    would let ``True`` masquerade as a quantity of one.

    Args:
        value: The candidate value.
        field: Name used in the error message, so a caller sees which input failed.
        positive: Require the value to be strictly greater than zero.
        non_negative: Require the value to be zero or greater.

    Returns:
        The value, as a :class:`~decimal.Decimal`.

    Raises:
        RoundingError: if the value is not a finite Decimal, or violates a bound.
    """
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise RoundingError(f"{field} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise RoundingError(f"{field} must be finite, got {value!r}")
    if positive and value <= 0:
        raise RoundingError(f"{field} must be positive, got {value!r}")
    if non_negative and value < 0:
        raise RoundingError(f"{field} must not be negative, got {value!r}")
    return value


def floor_to_step(value: Decimal, *, precision: int) -> Decimal:
    """Quantize ``value`` down to ``10 ** -precision``.

    The result is expressed *at* the step, including trailing zeros, so a quantity
    sent to a venue carries exactly the precision the venue publishes and never a
    longer fraction that would round again somewhere else.

    Args:
        value: The quantity to quantize.
        precision: Number of decimal places to keep, normally the venue's
            ``trade_units_precision``.

    Returns:
        The quantized Decimal.

    Raises:
        RoundingError: if the value is not a finite Decimal or the precision is
            not a non-negative integer.
    """
    require_decimal(value, field="value")
    if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
        raise RoundingError(f"precision must be a non-negative int, got {precision!r}")
    return value.quantize(Decimal(1).scaleb(-precision), rounding=_ROUNDING)
