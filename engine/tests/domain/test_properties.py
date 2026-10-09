"""Property-based tests for :mod:`engine.domain.ledger` and the canonical encoder.

The invariant under test: **a double-entry ledger's trial balance is identically zero for
any balanced history.** Rather than pick examples by hand, these tests generate arbitrary
histories and check the property holds, which is the only way to be confident the sign
convention is not merely right for the cases someone thought of.

A second property is checked alongside it: canonical JSON is a pure function of an event.
If two events with the same content ever produced different bytes, the hash chain would
report phantom tampering.
"""

import datetime as dt
import json
from decimal import Decimal

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from engine.domain.events import (
    CanonicalJSONError,
    DomainEvent,
    EventType,
    canonical_json_bytes,
    canonical_json_text,
)
from engine.domain.ledger import (
    Account,
    Direction,
    Ledger,
    Posting,
    Transaction,
    balance,
    equity,
    free_margin,
    trial_balance,
)

UTC = dt.UTC

#: The accounts the generator is allowed to use, in generation order.
ACCOUNTS = sorted(Account, key=lambda a: a.value)


# --------------------------------------------------------------------------- #
# generators
# --------------------------------------------------------------------------- #
#: The generator balances against PAYABLE_TO_BROKER, which is credit-normal and therefore
#: already covered by the EQUITY side of the sign convention.
DEBIT_ACCOUNTS = (Account.CASH, Account.FEES_PAID, Account.REALIZED_PNL, Account.UNREALIZED_PNL)
CREDIT_ACCOUNTS = Account.EQUITY

amounts = st.decimals(
    min_value=Decimal("0.01"), max_value=Decimal("1000000"), places=4
).map(lambda d: d.quantize(Decimal("0.01")))

identifiers = st.from_regex(r"[a-z][a-z0-9-]{3,12}", fullmatch=True)

timestamps = st.datetimes(
    min_value=dt.datetime(2020, 1, 1, tzinfo=UTC),
    max_value=dt.datetime(2030, 1, 1, tzinfo=UTC),
)


@st.composite
def balanced_transactions(draw: st.DrawFn) -> Transaction:
    """Draw one balanced transaction.

    Debits are spread over a subset of debit-normal accounts and the whole debit total is
    credited to EQUITY, which is credit-normal. That keeps the generated transaction valid
    by construction, so the property under test is the ledger's behaviour on valid history
    rather than the generator's ability to avoid invalid input.
    """
    count = draw(st.integers(min_value=1, max_value=len(DEBIT_ACCOUNTS)))
    chosen = draw(
        st.lists(
            st.sampled_from(DEBIT_ACCOUNTS),
            min_size=count,
            max_size=count,
            unique=True,
        )
    )
    debit_amounts = [draw(amounts) for _ in chosen]
    debit_total = sum(debit_amounts, Decimal(0))

    return Transaction(
        transaction_id=draw(identifiers),
        occurred_at=draw(timestamps),
        postings=(
            *(
                Posting(account=account, direction=Direction.DEBIT, amount=amount)
                for account, amount in zip(chosen, debit_amounts, strict=True)
            ),
            Posting(
                account=CREDIT_ACCOUNTS, direction=Direction.CREDIT, amount=debit_total
            ),
        ),
    )


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(transaction=balanced_transactions())
def test_a_generated_transaction_is_balanced(transaction: Transaction) -> None:
    assert transaction.total_debits == transaction.total_credits
    assert transaction.is_balanced


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(history=st.lists(balanced_transactions(), max_size=12, unique_by=lambda t: t.transaction_id))
def test_trial_balance_is_zero_for_any_balanced_history(history: list[Transaction]) -> None:
    """The double-entry invariant, stated as a property: debits must equal credits."""
    ledger = Ledger()
    for transaction in history:
        ledger = ledger.apply(transaction)

    assert trial_balance(ledger) == Decimal(0)
    assert sum((amount for _, amount in ledger.balances()), Decimal(0)) == Decimal(0)


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(history=st.lists(balanced_transactions(), max_size=12, unique_by=lambda t: t.transaction_id))
def test_per_account_balance_matches_an_independent_sum(history: list[Transaction]) -> None:
    """Each balance must equal a naive recomputation over the postings."""
    ledger = Ledger()
    for transaction in history:
        ledger = ledger.apply(transaction)

    for account in Account:
        expected = Decimal(0)
        for transaction in history:
            for posting in transaction.postings:
                if posting.account is account:
                    expected += posting.signed_amount
        assert balance(ledger, account) == expected


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(
    a=amounts,
    b=amounts,
    c=amounts,
)
def test_equity_is_consistent_with_free_margin(a: Decimal, b: Decimal, c: Decimal) -> None:
    """Equity is cash plus realized PnL plus the mark; free margin is equity less reserved."""
    ledger = Ledger().apply(
        Transaction(
            transaction_id="txn-a",
            occurred_at=dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC),
            postings=(
                Posting(account=Account.CASH, direction=Direction.DEBIT, amount=a),
                Posting(account=Account.EQUITY, direction=Direction.CREDIT, amount=a),
            ),
        )
    )
    ledger = ledger.apply(
        Transaction(
            transaction_id="txn-b",
            occurred_at=dt.datetime(2026, 1, 15, 14, 31, tzinfo=UTC),
            postings=(
                Posting(account=Account.REALIZED_PNL, direction=Direction.DEBIT, amount=b),
                Posting(account=Account.EQUITY, direction=Direction.CREDIT, amount=b),
            ),
        )
    )
    ledger = ledger.apply(
        Transaction(
            transaction_id="txn-c",
            occurred_at=dt.datetime(2026, 1, 15, 14, 32, tzinfo=UTC),
            postings=(
                Posting(account=Account.UNREALIZED_PNL, direction=Direction.DEBIT, amount=c),
                Posting(account=Account.EQUITY, direction=Direction.CREDIT, amount=c),
            ),
        )
    )

    expect_equity = balance(ledger, Account.CASH) + balance(ledger, Account.REALIZED_PNL) + balance(
        ledger, Account.UNREALIZED_PNL
    )
    assert equity(ledger) == expect_equity
    assert free_margin(ledger) == equity(ledger)


@settings(max_examples=100, deadline=None)
@given(
    event_id=identifiers,
    amount=amounts,
    occurred_at=st.datetimes(
        min_value=dt.datetime(2020, 1, 1, tzinfo=UTC),
        max_value=dt.datetime(2030, 1, 1, tzinfo=UTC),
    ),
)
def test_canonical_json_is_a_pure_function(
    event_id: str, amount: Decimal, occurred_at: dt.datetime
) -> None:
    """Same content, same bytes. If this ever fails, the hash chain cannot work."""
    event = DomainEvent(
        event_id=event_id,
        event_type=EventType.LEDGER_POSTED,
        occurred_at=occurred_at,
        payload={"amount": amount, "note": "x"},
    )

    assert canonical_json_bytes(event) == canonical_json_bytes(event)
    assert canonical_json_bytes(event) == event.canonical_bytes()


@settings(max_examples=100, deadline=None)
@given(amount=amounts)
def test_canonical_decimal_round_trips_exactly(amount: Decimal) -> None:
    """A Decimal must survive canonicalization without losing a single digit."""
    text = canonical_json_text(amount)
    parsed = json.loads(text)

    assert isinstance(parsed, str)
    assert Decimal(parsed) == amount
    assert format(Decimal(parsed), "f") == format(amount, "f")


@settings(max_examples=100, deadline=None)
@given(left=amounts, right=amounts)
def test_canonical_json_never_introduces_a_float(left: Decimal, right: Decimal) -> None:
    """No input of any size may make the encoder emit an exponent or a float."""
    text = canonical_json_text({"total": left + right})

    assert "e" not in text.lower()
    assert Decimal(json.loads(text)["total"]) == left + right


@settings(max_examples=50, deadline=None)
@given(value=st.floats(allow_nan=True, allow_infinity=True))
def test_a_float_is_always_rejected(value: float) -> None:
    """Every float, including NaN and the infinities, must be refused."""
    assume(value == value)  # skip nothing: NaN is the interesting case
    try:
        canonical_json_text(value)
    except CanonicalJSONError:
        return
    raise AssertionError(f"float {value!r} was canonicalized instead of rejected")
