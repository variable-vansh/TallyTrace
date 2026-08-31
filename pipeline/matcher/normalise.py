"""Sign normalisation.

Platforms do not agree on how to write a reversal. Amazon, Myntra and the website
negate the amount and put the money back through the credit column; Flipkart and the
POS keep the amount positive and put it in the debit column. Same money, two
conventions, and neither is wrong -- so the matcher normalises rather than assuming.

The normalised convention is one rule: **negative is money leaving the seller.** A
payment's net is positive and its fee is negative; a reversal's net is negative and
its fee -- being given back -- is positive.

Direction is read off the row's own net rather than off its ``type``, because the
type field is what platforms are least consistent about: an RTO deduction arrives as
a ``refund`` on one channel and an ``adjustment`` on another, and both mean the same
thing to the bank.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from pipeline.models import SettlementRow

PAISE = Decimal("0.01")
ZERO = Decimal("0.00")


def inr(value: Decimal) -> Decimal:
    """Quantize to paise, half away from zero, with negative zero normalised out."""
    result = Decimal(value).quantize(PAISE, rounding=ROUND_HALF_UP)
    return result + ZERO if result == ZERO else result


@dataclass(frozen=True)
class NormalisedRow:
    """One settlement row in the matcher's own sign convention."""

    row: SettlementRow
    net: Decimal          # rupees this row moves into the bank; negative is out
    fee: Decimal          # negative when charged, positive when reversed
    tax: Decimal
    tcs: Decimal
    tds: Decimal

    @property
    def entity_id(self) -> str:
        return self.row.entity_id

    @property
    def order_id(self) -> str | None:
        return self.row.order_id

    @property
    def utr(self) -> str:
        return self.row.settlement_utr


def raw_net(row: SettlementRow) -> Decimal:
    """``credit - debit``: what the bank actually moved, in either convention."""
    return inr(row.credit - row.debit)


def normalise(row: SettlementRow) -> NormalisedRow:
    """Put one row into the matcher's convention.

    A row that moves no money -- a fully withheld payment, say -- is treated as an
    inbound row, so its fee still reads as charged. That is the truth of a hold: the
    platform kept the commission and paid nothing out.
    """
    net = raw_net(row)
    outbound = net < ZERO
    sign = Decimal(1) if outbound else Decimal(-1)
    return NormalisedRow(
        row=row,
        net=net,
        fee=inr(abs(row.fee) * sign),
        tax=inr(abs(row.tax) * sign),
        tcs=inr(abs(row.tcs) * sign),
        tds=inr(abs(row.tds) * sign),
    )


def normalise_all(rows: list[SettlementRow]) -> list[NormalisedRow]:
    return [normalise(row) for row in rows]


def total_net(rows: list[NormalisedRow]) -> Decimal:
    return inr(sum((row.net for row in rows), ZERO))


def charged_fee(rows: list[NormalisedRow]) -> Decimal:
    """Fee the platform kept across these rows, positive when it kept money.

    Flipped back to the ledger's convention -- ``expected_fee`` is a positive cost --
    so the two sides of the value comparison read the same way round.
    """
    return inr(-sum((row.fee for row in rows), ZERO))
