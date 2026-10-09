"""Unit tests for :mod:`engine.domain.ledger`.

Double-entry means every transaction's debits equal its credits, so the ledger's trial
balance is *identically* zero. These tests pin that at the unit level; the Hypothesis
property tests in ``test_properties.py`` pin it for arbitrarily many transactions.

Flavourable constructor checks are as important here as the arithmetic: an unbalanced
transaction must be refused at construction, not silently accepted and "fixed" on read.
"""

import datetime as dt
from decimal import Decimal

import pytest

from engine.domain.ledger import (
    Account,
    Direction,
    Ledger,
    LedgerError,
    Posting,
    Transaction,
    balance,
    equity,
    free_margin,
    trial_balance,
)

UTC = dt.UTC
AT = dt.datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def _posting(account: Account, direction: Direction, amount: str) -> Posting:
    return Posting(account=account, direction=direction, amount=Decimal(amount))


def _receipt(transaction_id: str = "txn-1", amount: str = "1000.00") -> Transaction:
    """Deposit cash: debit CASH, credit EQUITY (owner's claim)."""
    return Transaction(
        transaction_id=transaction_id,
        occurred_at=AT,
        postings=(
            _posting(Account.CASH, Direction.DEBIT, amount),
            _posting(Account.EQUITY, Direction.CREDIT, amount),
        ),
    )


# --------------------------------------------------------------------------- #
# enums
# --------------------------------------------------------------------------- #
def test_account_wire_values_are_unique() -> None:
    values = [a.value for a in Account]
    assert len(values) == len(set(values))


def test_account_covers_the_balance_sheet() -> None:
    expected = {
        "CASH",
        "EQUITY",
        "REALIZED_PNL",
        "UNREALIZED_PNL",
        "FEES_PAID",
        "MARGIN_USED",
        "MARGIN_FREE",
        "PAYABLE_TO_BROKER",
    }
    assert {a.value for a in Account} == expected


def test_account_from_value() -> None:
    assert Account.from_value("cash") is Account.CASH
    assert Account.from_value("REALIZED_PNL") is Account.REALIZED_PNL
    assert Account.from_value("nonsense") is None


def test_direction_sign() -> None:
    assert Direction.DEBIT.sign == Decimal(1)
    assert Direction.CREDIT.sign == Decimal(-1)


# --------------------------------------------------------------------------- #
# Posting / Transaction validation
# --------------------------------------------------------------------------- #
def test_posting_is_frozen() -> None:
    posting = _posting(Account.CASH, Direction.DEBIT, "100")

    with pytest.raises(Exception):  # noqa: B017
        posting.amount = Decimal("200")  # type: ignore[misc]


@pytest.mark.parametrize("amount", ["0", "-1", "NaN", "Infinity"])
def test_posting_rejects_non_positive_or_non_finite(amount: str) -> None:
    with pytest.raises(ValueError, match="amount"):
        _posting(Account.CASH, Direction.DEBIT, amount)


def test_posting_rejects_a_float_amount() -> None:
    with pytest.raises(ValueError, match="Decimal"):
        Posting(account=Account.CASH, direction=Direction.DEBIT, amount=100.0)  # type: ignore[arg-type]


def test_transaction_rejects_an_unbalanced_body() -> None:
    """A transaction whose debits differ from its credits must not construct."""
    with pytest.raises(LedgerError, match="unbalanced"):
        Transaction(
            transaction_id="txn-bad",
            occurred_at=AT,
            postings=(_posting(Account.CASH, Direction.DEBIT, "100"),),
        )


def test_transaction_rejects_duplicate_accounts_in_one_direction() -> None:
    """Two debits to the same account must be a single posting, or the trial balance lies."""
    with pytest.raises(LedgerError, match="posts twice"):
        Transaction(
            transaction_id="txn-dup",
            occurred_at=AT,
            postings=(
                _posting(Account.CASH, Direction.DEBIT, "100"),
                _posting(Account.CASH, Direction.DEBIT, "100"),
                _posting(Account.EQUITY, Direction.CREDIT, "200"),
            ),
        )


def test_transaction_requires_a_naive_timestamp_rejection() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Transaction(
            transaction_id="txn-2",
            occurred_at=dt.datetime(2026, 1, 15, 14, 30),
            postings=_receipt().postings,
        )


def test_transaction_requires_at_least_two_postings() -> None:
    """An empty posting list balances trivially, so it is refused separately."""
    with pytest.raises(LedgerError, match="at least two"):
        Transaction(
            transaction_id="txn-3",
            occurred_at=AT,
            postings=(),
        )


def test_transaction_with_one_posting_reports_unbalanced() -> None:
    """A single non-zero posting cannot balance, and says so."""
    with pytest.raises(LedgerError, match="unbalanced"):
        Transaction(
            transaction_id="txn-4",
            occurred_at=AT,
            postings=(_posting(Account.CASH, Direction.DEBIT, "100"),),
        )


def test_transaction_totals() -> None:
    transaction = _receipt()

    assert transaction.total_debits == Decimal("1000.00")
    assert transaction.total_credits == Decimal("1000.00")
    assert transaction.is_balanced is True


# --------------------------------------------------------------------------- #
# Ledger
# --------------------------------------------------------------------------- #
def test_empty_ledger_is_all_zero() -> None:
    ledger = Ledger()

    assert balance(ledger, Account.CASH) == Decimal(0)
    assert equity(ledger) == Decimal(0)
    assert free_margin(ledger) == Decimal(0)
    assert trial_balance(ledger) == Decimal(0)


def test_apply_returns_a_new_ledger() -> None:
    ledger = Ledger()
    updated = ledger.apply(_receipt())

    assert ledger is not updated
    assert balance(ledger, Account.CASH) == Decimal(0)
    assert balance(updated, Account.CASH) == Decimal("1000.00")


def test_apply_rejects_an_unbalanced_transaction() -> None:
    ledger = Ledger()

    with pytest.raises(LedgerError, match="unbalanced"):
        ledger.apply(
            Transaction(
                transaction_id="txn-bad",
                occurred_at=AT,
                postings=(_posting(Account.CASH, Direction.DEBIT, "100"),),
            )
        )


def test_cash_receipt_increases_equity() -> None:
    ledger = Ledger().apply(_receipt())

    assert equity(ledger) == Decimal("1000.00")


def test_multiple_receipts_accumulate() -> None:
    ledger = Ledger().apply(_receipt("txn-1")).apply(_receipt("txn-2"))

    assert balance(ledger, Account.CASH) == Decimal("2000.00")
    assert equity(ledger) == Decimal("2000.00")


def test_fees_reduce_equity() -> None:
    """Fees are an expense: debit FEES_PAID, credit CASH."""
    ledger = Ledger()
    ledger = ledger.apply(_receipt())
    ledger = ledger.apply(
        Transaction(
            transaction_id="txn-fee",
            occurred_at=AT,
            postings=(
                _posting(Account.FEES_PAID, Direction.DEBIT, "2.50"),
                _posting(Account.CASH, Direction.CREDIT, "2.50"),
            ),
        )
    )

    assert balance(ledger, Account.FEES_PAID) == Decimal("2.50")
    assert balance(ledger, Account.CASH) == Decimal("997.50")


def test_margin_reservation_blocks_free_margin() -> None:
    ledger = Ledger().apply(_receipt())
    ledger = ledger.apply(
        Transaction(
            transaction_id="txn-margin",
            occurred_at=AT,
            postings=(
                _posting(Account.MARGIN_USED, Direction.DEBIT, "100.00"),
                _posting(Account.CASH, Direction.CREDIT, "100.00"),
            ),
        )
    )

    assert balance(ledger, Account.MARGIN_USED) == Decimal("100.00")
    assert free_margin(ledger) == Decimal("900.00")


def test_free_margin_is_equity_less_margin_used() -> None:
    ledger = Ledger().apply(_receipt())

    assert free_margin(ledger) == equity(ledger)


def test_unrealized_pnl_contributes_to_equity_only_when_marked() -> None:
    ledger = Ledger().apply(_receipt())
    ledger = ledger.apply(
        Transaction(
            transaction_id="txn-mark",
            occurred_at=AT,
            postings=(
                _posting(Account.UNREALIZED_PNL, Direction.DEBIT, "50.00"),
                _posting(Account.EQUITY, Direction.CREDIT, "50.00"),
            ),
        )
    )

    assert balance(ledger, Account.UNREALIZED_PNL) == Decimal("50.00")
    assert equity(ledger) == Decimal("1050.00")


def test_trial_balance_is_zero_for_any_balanced_history() -> None:
    ledger = (
        Ledger()
        .apply(_receipt())
        .apply(
            Transaction(
                transaction_id="txn-fee",
                occurred_at=AT.replace(minute=31),
                postings=(
                    _posting(Account.FEES_PAID, Direction.DEBIT, "2.50"),
                    _posting(Account.CASH, Direction.CREDIT, "2.50"),
                ),
            )
        )
        .apply(
            Transaction(
                transaction_id="txn-pnl",
                occurred_at=AT.replace(minute=32),
                postings=(
                    _posting(Account.REALIZED_PNL, Direction.DEBIT, "17.25"),
                    _posting(Account.EQUITY, Direction.CREDIT, "17.25"),
                ),
            )
        )
    )

    assert trial_balance(ledger) == Decimal(0)
    assert balance(ledger, Account.CASH) == Decimal("997.50")
    assert balance(ledger, Account.REALIZED_PNL) == Decimal("17.25")


# --------------------------------------------------------------------------- #
# balance semantics
# --------------------------------------------------------------------------- #
def test_balance_is_debits_less_credits() -> None:
    """The convention: a balance is debits less credits.

    ``CASH`` is debit-normal, so a deposit (a debit) raises it and it reads positive.
    ``EQUITY`` is credit-normal, so the matching credit raises it and it reads negative.
    That asymmetry is inherent -- no single uniform formula can make both kinds of account
    positive at once, and it is exactly what makes the trial balance come out zero.
    :func:`equity` resolves the sign so callers never handle it.
    """
    ledger = Ledger().apply(_receipt())

    assert balance(ledger, Account.CASH) == Decimal("1000.00")
    assert balance(ledger, Account.EQUITY) == Decimal("-1000.00")
    assert equity(ledger) == Decimal("1000.00")


def test_ledger_balances_snapshot_is_sorted() -> None:
    ledger = Ledger().apply(_receipt())
    accounts = [a for a, _ in ledger.balances()]

    assert accounts == sorted(accounts)


def test_ledger_balances_excludes_zero_accounts() -> None:
    ledger = Ledger().apply(_receipt())
    present = {a for a, _ in ledger.balances()}

    assert Account.FEES_PAID not in present
    assert Account.CASH in present


def test_ledger_records_every_transaction_id() -> None:
    ledger = Ledger().apply(_receipt())

    assert ledger.transaction_count == 1
    assert ledger.posting_count == 2


def test_ledger_apply_is_immutable_under_reuse() -> None:
    first = Ledger().apply(_receipt("txn-1"))
    second = first.apply(_receipt("txn-2"))

    assert first.transaction_count == 1
    assert second.transaction_count == 2
    assert balance(second, Account.CASH) == Decimal("2000.00")
    assert balance(first, Account.CASH) == Decimal("1000.00")


def test_ledger_rejects_a_non_transaction() -> None:
    with pytest.raises(TypeError, match="Transaction"):
        Ledger().apply([("not", "a", "transaction")])  # type: ignore[arg-type]


def test_ledger_equity_never_involves_a_float() -> None:
    ledger = Ledger().apply(_receipt())

    assert isinstance(equity(ledger), Decimal)
    assert isinstance(free_margin(ledger), Decimal)
    assert isinstance(trial_balance(ledger), Decimal)


def test_ledger_apply_with_the_same_transaction_id_is_idempotent() -> None:
    """Re-applying an identical transaction must not double-count it.

    Replay is the reason: a journal replayed from the start must produce the same ledger
    as the live one, and a repeated tail must not silently inflate balances.
    """
    receipt = _receipt()
    ledger = Ledger().apply(receipt)

    with pytest.raises(LedgerError, match="already applied"):
        ledger.apply(receipt)


def test_ledger_history_is_iterable() -> None:
    ledger = Ledger().apply(_receipt())

    assert [t.transaction_id for t in ledger.history()] == ["txn-1"]


def test_ledger_posting_count_matches_history() -> None:
    ledger = Ledger().apply(_receipt())

    assert ledger.posting_count == sum(len(t.postings) for t in ledger.history())
