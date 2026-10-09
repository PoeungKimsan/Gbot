"""Tests for fixed-fractional position sizing.

The invariants under test, in order of how much money they protect:

* **Risk is budgeted, never promised.** ``risk_amount = equity * risk_per_trade`` and the
  position is floored to the venue's trade-unit precision. Flooring can only ever leave a
  little of the budget unused, so the loss from a stop-out is at most the amount risked and
  never more. That is the pessimistic direction, and AGENTS.md 2.5 insists on it.
* **Venue geometry comes from the spec.** The rounding precision and the minimum size are
  read from :class:`~engine.market.instrument.InstrumentSpec`, never from a literal in this
  package. For XAU_USD, one unit is one troy ounce and ``quantity * price`` is the notional,
  which is the same convention :mod:`engine.domain.positions` uses.
* **A too-small position is refused**, not silently scaled up to reach the minimum.
"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from engine.market.instrument import InstrumentSpec
from engine.risk.sizing import (
    BelowMinimumTradeSize,
    PositionSize,
    SizingError,
    r_multiple,
    size_position,
)

XAU_USD = InstrumentSpec(
    name="XAU_USD",
    pip_location=-4,
    display_precision=2,
    trade_units_precision=5,
    minimum_trade_size=Decimal("1"),
)

WHOLE_UNITS = InstrumentSpec(
    name="WHOLE_UNITS",
    pip_location=-2,
    display_precision=2,
    trade_units_precision=0,
    minimum_trade_size=Decimal("1"),
)


def _size(**overrides: object) -> PositionSize:
    defaults: dict[str, object] = {
        "equity": Decimal("100000"),
        "stop_distance": Decimal("10"),
        "risk_per_trade": Decimal("0.01"),
        "spec": XAU_USD,
    }
    defaults.update(overrides)
    return size_position(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# the sizing formula
# --------------------------------------------------------------------------- #
def test_risk_amount_is_equity_times_the_fraction() -> None:
    size = _size()

    assert size.risk_amount == Decimal("1000")
    assert size.units == Decimal("100")


def test_exact_division_needs_no_rounding() -> None:
    size = _size(
        equity=Decimal("50000"), risk_per_trade=Decimal("0.02"), stop_distance=Decimal("10")
    )

    assert size.risk_amount == Decimal("1000")
    assert size.units == Decimal("100")
    assert size.worst_case_loss == Decimal("1000")


def test_units_floor_to_the_venue_precision() -> None:
    size = _size(stop_distance=Decimal("7"))

    assert size.risk_amount == Decimal("1000")
    # 1000 / 7 = 142.857142857... -> floored to five places.
    assert size.units == Decimal("142.85714")
    assert size.units.as_tuple().exponent == -XAU_USD.trade_units_precision


def test_units_are_expressed_at_venue_precision_even_when_exact() -> None:
    """A caller must be able to send the quantity straight to the venue."""
    size = _size()

    assert size.units == Decimal("100")
    assert size.units.as_tuple().exponent == -5
    assert str(size.units) == "100.00000"


def test_a_precision_zero_venue_yields_whole_units() -> None:
    size = _size(spec=WHOLE_UNITS, stop_distance=Decimal("3"))

    assert size.risk_amount == Decimal("1000")
    assert size.units == Decimal("333")
    assert size.units.as_tuple().exponent == 0


def test_a_larger_equity_scales_the_position_linearly() -> None:
    assert _size(equity=Decimal("200000")).units == Decimal("200")


def test_a_tighter_stop_takes_a_larger_position() -> None:
    assert _size(stop_distance=Decimal("5")).units == Decimal("200")


# --------------------------------------------------------------------------- #
# the pessimistic direction: a stop-out can never lose more than budgeted
# --------------------------------------------------------------------------- #
def test_worst_case_loss_never_exceeds_the_risk_amount() -> None:
    for stop in ("7", "3", "1.37", "0.45", "11"):
        size = _size(stop_distance=Decimal(stop))
        assert size.worst_case_loss <= size.risk_amount, stop


def test_worst_case_loss_is_the_stop_distance_times_units() -> None:
    size = _size(stop_distance=Decimal("7"))

    assert size.worst_case_loss == size.stop_distance * size.units
    assert size.worst_case_loss == Decimal("999.99998")


def test_flooring_can_only_leave_risk_unused_not_exceed_it() -> None:
    """1000/3 floors to 333.33333 units, so all but 0.00001 of the budget is used."""
    size = _size(stop_distance=Decimal("3"))

    assert size.units == Decimal("333.33333")
    assert size.worst_case_loss == Decimal("999.99999")
    assert size.risk_amount - size.worst_case_loss == Decimal("0.00001")
    assert size.risk_used == Decimal("0.99999999")


# --------------------------------------------------------------------------- #
# venue geometry
# --------------------------------------------------------------------------- #
def test_notional_uses_the_same_convention_as_domain_positions() -> None:
    size = _size()

    # One unit is one ounce, so quantity * price is the notional. This is the
    # formula engine.domain.positions.Position.notional uses; sizing does not
    # invent a contract multiplier of its own.
    assert size.notional_at(Decimal("2038.25")) == size.units * Decimal("2038.25")


def test_precision_and_minimum_come_from_the_spec() -> None:
    precise = InstrumentSpec(
        name="PRECISE",
        pip_location=-4,
        display_precision=2,
        trade_units_precision=2,
        minimum_trade_size=Decimal("0.10"),
    )
    size = _size(spec=precise, stop_distance=Decimal("7"))

    assert size.units.as_tuple().exponent == -2
    assert size.units == Decimal("142.85")


def test_announces_its_spec_so_a_caller_can_journal_the_geometry() -> None:
    assert _size().spec.name == "XAU_USD"


# --------------------------------------------------------------------------- #
# refusals
# --------------------------------------------------------------------------- #
def test_a_position_below_the_minimum_is_refused() -> None:
    with pytest.raises(BelowMinimumTradeSize):
        _size(equity=Decimal("500"), stop_distance=Decimal("10"))


def test_a_position_that_floors_to_zero_is_refused() -> None:
    with pytest.raises(BelowMinimumTradeSize):
        _size(equity=Decimal("50"), stop_distance=Decimal("10"))


def test_a_position_just_below_the_minimum_is_refused() -> None:
    spec = InstrumentSpec(
        name="BIG_MIN",
        pip_location=-4,
        display_precision=2,
        trade_units_precision=0,
        minimum_trade_size=Decimal("10"),
    )
    with pytest.raises(BelowMinimumTradeSize):
        _size(spec=spec, stop_distance=Decimal("101"))


def test_a_position_exactly_at_the_minimum_is_accepted() -> None:
    # risk 1000, stop 1000 -> 1 unit, and the minimum is 1.
    size = _size(equity=Decimal("100000"), stop_distance=Decimal("1000"))

    assert size.units == Decimal("1")
    assert size.worst_case_loss == Decimal("1000")


def test_below_minimum_is_a_sizing_error() -> None:
    assert issubclass(BelowMinimumTradeSize, SizingError)


def test_a_zero_stop_distance_is_refused() -> None:
    with pytest.raises(SizingError):
        _size(stop_distance=Decimal("0"))


def test_a_negative_stop_distance_is_refused() -> None:
    with pytest.raises(SizingError):
        _size(stop_distance=Decimal("-10"))


def test_a_zero_equity_is_refused() -> None:
    with pytest.raises(SizingError):
        _size(equity=Decimal("0"))


def test_a_negative_equity_is_refused() -> None:
    with pytest.raises(SizingError):
        _size(equity=Decimal("-100000"))


def test_a_zero_risk_fraction_is_refused() -> None:
    with pytest.raises(SizingError):
        _size(risk_per_trade=Decimal("0"))


def test_a_risk_fraction_above_one_is_refused() -> None:
    with pytest.raises(SizingError):
        _size(risk_per_trade=Decimal("2"))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"equity": 100000.0},
        {"stop_distance": 10.0},
        {"risk_per_trade": 0.01},
        {"equity": float("100000")},
    ],
)
def test_a_float_input_is_refused(kwargs: dict[str, object]) -> None:
    """AGENTS.md 2.1: a float must not reach position sizing at all."""
    with pytest.raises(SizingError):
        _size(**kwargs)  # type: ignore[arg-type]


def test_a_non_finite_equity_is_refused() -> None:
    with pytest.raises(SizingError):
        _size(equity=Decimal("NaN"))


def test_a_bool_is_not_a_decimal() -> None:
    with pytest.raises(SizingError):
        _size(equity=True)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# R multiples
# --------------------------------------------------------------------------- #
def test_r_multiple_of_a_stopped_out_trade() -> None:
    assert r_multiple(Decimal("-500"), Decimal("1000")) == Decimal("-0.5")


def test_r_multiple_of_a_target_hit() -> None:
    assert r_multiple(Decimal("2000"), Decimal("1000")) == Decimal("2")


def test_r_multiple_of_a_scratch_trade() -> None:
    assert r_multiple(Decimal("0"), Decimal("1000")) == Decimal("0")


def test_r_multiple_refuses_a_zero_risk_amount() -> None:
    with pytest.raises(SizingError):
        r_multiple(Decimal("100"), Decimal("0"))


def test_r_multiple_refuses_a_float_pnl() -> None:
    with pytest.raises(SizingError):
        r_multiple(-500.0, Decimal("1000"))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# properties
# --------------------------------------------------------------------------- #
@given(
    equity=st.decimals(
        min_value="1", max_value="10_000_000", allow_nan=False, allow_infinity=False, places=2
    ),
    stop=st.decimals(
        min_value="0.01", max_value="500", allow_nan=False, allow_infinity=False, places=2
    ),
    fraction=st.decimals(
        min_value="0.001", max_value="1", allow_nan=False, allow_infinity=False, places=4
    ),
)
def test_flooring_never_risks_more_than_budgeted(
    equity: Decimal, stop: Decimal, fraction: Decimal
) -> None:
    """Whatever the inputs, a stop-out costs no more than was risked.

    AGENTS.md 2.5 is a direction, not an average: over arbitrary inputs the floored
    quantity may leave part of the budget unused, but it may never commit more of the
    account than the fraction allowed.
    """
    try:
        size = size_position(
            equity=equity, stop_distance=stop, risk_per_trade=fraction, spec=XAU_USD
        )
    except BelowMinimumTradeSize:
        # Refused before any position exists, which is the other safe outcome.
        return

    assert size.worst_case_loss <= size.risk_amount
    assert size.units >= XAU_USD.minimum_trade_size
    assert size.units.as_tuple().exponent == -XAU_USD.trade_units_precision


@given(
    stop=st.decimals(
        min_value="0.01", max_value="500", allow_nan=False, allow_infinity=False, places=2
    )
)
def test_a_tighter_stop_never_takes_a_smaller_position(stop: Decimal) -> None:
    """A stop twice as far can never size up: the budget is what it is."""
    near = _size(stop_distance=stop)
    far = _size(stop_distance=stop * Decimal("2"))

    assert far.units <= near.units
    assert far.worst_case_loss <= near.risk_amount
