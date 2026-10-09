"""Double-entry ledger: balance, equity, margin, and realized PnL in Decimal.

Double-entry bookkeeping is used because it is *self-checking*. Every transaction postings to
at least two accounts, with debits equal to credits, so the sum of all account balances is
identically zero. Any bug that miscalculates a balance in one place shows up as a non-zero
trial balance rather than as a plausible-looking wrong number.

Sign convention
---------------
An account's balance is **debits less credits**:

* ``CASH``, ``FEES_PAID``, ``REALIZED_PNL``, ``UNREALIZED_PNL`` and ``MARGIN_USED`` are
  **debit-normal**: a debit raises the balance, so they all read as positive numbers.
* ``EQUITY`` and ``PAYABLE_TO_BROKER`` are **credit-normal**: a credit raises them, so
  their raw balances read as negative and :func:`equity` negates ``EQUITY`` back to a
  positive figure. No uniform formula can make both kinds of account positive at once --
  that asymmetry is what makes the trial balance exactly zero.

Equity is everything the account is worth: cash settled, realized profit and loss, and the
unrealized mark. Free margin is what is left after the broker has reserved margin.

Everything here is a :class:`~decimal.Decimal`. No float is accepted at any boundary, and
:func:`balance` returns ``Decimal(0)`` rather than a float zero.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "Account",
    "Direction",
    "Ledger",
    "LedgerError",
    "Posting",
    "Transaction",
    "balance",
    "equity",
    "free_margin",
    "trial_balance",
]


class LedgerError(RuntimeError):
    """A transaction is unbalanced, duplicated, or otherwise inadmissible."""


class Account(StrEnum):
    """Balance-sheet accounts.

    Wire values are stored in the journal, so a rename is a schema change.
    """

    CASH = "CASH"
    EQUITY = "EQUITY"
    REALIZED_PNL = "REALIZED_PNL"
    UNREALIZED_PNL = "UNREALIZED_PNL"
    FEES_PAID = "FEES_PAID"
    MARGIN_USED = "MARGIN_USED"
    MARGIN_FREE = "MARGIN_FREE"
    PAYABLE_TO_BROKER = "PAYABLE_TO_BROKER"

    @classmethod
    def from_value(cls, value: Any) -> Account | None:
        if not isinstance(value, str):
            return None
        try:
            return cls(value.strip().upper())
        except ValueError:
            return None


class Direction(StrEnum):
    """Debit or credit."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"

    @property
    def sign(self) -> Decimal:
        """+1 for a debit, -1 for a credit.

        A balance is **debits less credits**, so a debit raises it and a credit lowers it.
        That makes debit-normal accounts (``CASH``, ``FEES_PAID``, ``REALIZED_PNL``,
        ``MARGIN_USED``) read as positive numbers, and makes credit-normal accounts
        (``EQUITY``, ``PAYABLE_TO_BROKER``) read as negative ones. The asymmetry is inherent
        to double-entry: no single uniform formula can make both kinds of account positive.
        :func:`equity` negates the credit-normal account, which is the one place a caller
        should never have to think about the sign.
        """
        return Decimal(1) if self is Direction.DEBIT else Decimal(-1)


@dataclass(frozen=True, slots=True)
class Posting:
    """One side of a transaction, against one account."""

    account: Account
    direction: Direction
    amount: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.account, Account):
            raise ValueError(
                f"account must be an Account, got {type(self.account).__name__}"
            )
        if not isinstance(self.direction, Direction):
            raise ValueError(
                f"direction must be a Direction, got {type(self.direction).__name__}"
            )
        _require_decimal(self.amount, field="amount", positive=True)

    @property
    def signed_amount(self) -> Decimal:
        """The amount with its direction's sign applied."""
        return self.amount * self.direction.sign


@dataclass(frozen=True, slots=True)
class Transaction:
    """A balanced set of postings.

    Debits equal credits, enforced here at construction. Refusing an unbalanced transaction
    is deliberate: accepting it and "fixing" it on read would hide the bug that produced it.
    """

    transaction_id: str
    occurred_at: dt.datetime
    postings: tuple[Posting, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.transaction_id, str) or not self.transaction_id.strip():
            raise ValueError(
                f"transaction_id must be a non-empty string, got {self.transaction_id!r}"
            )
        if not isinstance(self.occurred_at, dt.datetime) or self.occurred_at.tzinfo is None:
            raise ValueError(
                f"occurred_at must be timezone-aware, got {self.occurred_at!r}"
            )
        for posting in self.postings:
            if not isinstance(posting, Posting):
                raise LedgerError(
                    f"transaction {self.transaction_id} contains a non-Posting: {posting!r}"
                )

        if not self.postings:
            raise LedgerError(
                f"transaction {self.transaction_id} needs at least two postings"
            )
        self._reject_duplicate_accounts()
        # Unbalanced before the count check: a single posting is always unbalanced, and
        # "debits do not equal credits" explains the problem better than "too few postings".
        if not self.is_balanced:
            raise LedgerError(
                f"transaction {self.transaction_id} is unbalanced: "
                f"debits {self.total_debits} != credits {self.total_credits}"
            )

    def _reject_duplicate_accounts(self) -> None:
        """One posting per account, so the journal row per transaction is unambiguous.

        Two debits to CASH in one transaction would be legal double-entry but would make
        the stored payload lossy, because the journal keeps a single amount per account.
        """
        seen: set[Account] = set()
        for posting in self.postings:
            if posting.account in seen:
                raise LedgerError(
                    f"transaction {self.transaction_id} posts twice to "
                    f"{posting.account.value}; combine them into one posting"
                )
            seen.add(posting.account)

    @property
    def total_debits(self) -> Decimal:
        return sum(
            (p.amount for p in self.postings if p.direction is Direction.DEBIT),
            Decimal(0),
        )

    @property
    def total_credits(self) -> Decimal:
        return sum(
            (p.amount for p in self.postings if p.direction is Direction.CREDIT),
            Decimal(0),
        )

    @property
    def is_balanced(self) -> bool:
        return self.total_debits == self.total_credits

    def posting_for(self, account: Account, direction: Direction) -> Posting | None:
        for posting in self.postings:
            if posting.account is account and posting.direction is direction:
                return posting
        return None


@dataclass(frozen=True, slots=True)
class Ledger:
    """The full account history, folded into balances.

    Immutable by construction: :meth:`apply` returns a new ledger. The history is retained
    so a ledger can be replayed, diffed, and reconciled against the journal rather than
    rebuilt from a snapshot.
    """

    transactions: tuple[Transaction, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "transactions", tuple(self.transactions))

    # -- mutation (functional) ----------------------------------------------- #
    def apply(self, transaction: Transaction) -> Ledger:
        """Return a new ledger with ``transaction`` applied."""
        if not isinstance(transaction, Transaction):
            raise TypeError(
                f"expected a Transaction, got {type(transaction).__name__}"
            )
        if any(t.transaction_id == transaction.transaction_id for t in self.transactions):
            raise LedgerError(
                f"transaction {transaction.transaction_id} was already applied; "
                "replay must not double-count"
            )
        return Ledger(transactions=(*self.transactions, transaction))

    # -- queries -------------------------------------------------------------- #
    def balance(self, account: Account) -> Decimal:
        return balance(self, account)

    def balances(self) -> tuple[tuple[Account, Decimal], ...]:
        """Every non-zero account balance, sorted by account for stable output."""
        totals: dict[Account, Decimal] = {}
        for posting in self._postings():
            totals[posting.account] = (
                totals.get(posting.account, Decimal(0)) + posting.signed_amount
            )
        return tuple(
            (account, amount) for account,
                amount in sorted(totals.items(), key=lambda kv: kv[0].value)
            if amount != 0
        )

    def history(self) -> Iterator[Transaction]:
        return iter(self.transactions)

    @property
    def transaction_count(self) -> int:
        return len(self.transactions)

    @property
    def posting_count(self) -> int:
        return sum(len(t.postings) for t in self.transactions)

    def _postings(self) -> Iterator[Posting]:
        for transaction in self.transactions:
            yield from transaction.postings


def balance(ledger: Ledger, account: Account) -> Decimal:
    """Balance of one account, as credits less debits."""
    if not isinstance(ledger, Ledger):
        raise TypeError(f"expected a Ledger, got {type(ledger).__name__}")
    if not isinstance(account, Account):
        raise TypeError(f"expected an Account, got {type(account).__name__}")

    total = Decimal(0)
    for posting in ledger._postings():
        if posting.account is account:
            total += posting.signed_amount
    return total


def trial_balance(ledger: Ledger) -> Decimal:
    """Sum of all account balances.

    Exactly zero for any balanced history. Non-zero means a transaction was applied whose
    debits did not equal its credits, which the constructor already refuses, so a non-zero
    result here means the check was bypassed.
    """
    return sum((amount for _, amount in ledger.balances()), Decimal(0))


def equity(ledger: Ledger) -> Decimal:
    """Total account equity: settled cash, realized PnL, and the unrealized mark.

    ``EQUITY`` is credit-normal, so its raw balance is negative. This is the single place
    that sign is resolved, so callers get a positive equity and never handle the
    convention themselves.
    """
    return -balance(ledger, Account.EQUITY)


def free_margin(ledger: Ledger) -> Decimal:
    """Equity less the margin currently reserved by open positions."""
    return equity(ledger) - balance(ledger, Account.MARGIN_USED)


def _require_decimal(
    value: Any, *, field: str, positive: bool = False
) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError(f"{field} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{field} must be finite, got {value!r}")
    if positive and value <= 0:
        raise ValueError(f"{field} must be positive, got {value!r}")
    return value
