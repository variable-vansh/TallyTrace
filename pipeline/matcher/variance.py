"""Value-level variance detection.

For an order matched on both sides, compare what the platform charged against what
the books expected. The tolerance band comes from the ledger's own
``expected_commission_rate`` -- via ``expected_fee`` -- rather than from a rupee
constant, so a stale rate produces a variance proportional to the order rather than
a flat one. That proportionality is the signal: it is what makes fifty stale-rate
exceptions look like one rule instead of fifty coincidences.

Deliberately *not* here: any judgement about the cause. This module says "off by
₹132.44, outside a ₹7.51 band". Why it is off is checkpoint 3's question.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pipeline.matcher.normalise import NormalisedRow, ZERO, charged_fee, inr, total_net
from pipeline.matcher.reasons import Bucket, Reason
from pipeline.matcher.settings import MatchConfig
from pipeline.models import LedgerRow

HUNDRED = Decimal("100")
PCT = Decimal("0.01")


@dataclass(frozen=True)
class ValueCheck:
    """The outcome of comparing one order's settlement rows to its ledger row."""

    bucket: Bucket
    reason: Reason
    tolerance: Decimal
    expected_fee: Decimal
    charged_fee: Decimal
    expected_net: Decimal
    settled_net: Decimal

    @property
    def fee_delta(self) -> Decimal:
        return inr(self.charged_fee - self.expected_fee)

    @property
    def net_delta(self) -> Decimal:
        return inr(self.settled_net - self.expected_net)

    @property
    def fee_variance_pct(self) -> Decimal | None:
        """Fee delta as a percentage of the fee the books expected.

        This is the band a commission rule is written in -- "they are billing 24.2%
        against our 22%" is this number, not a rupee figure -- so the matcher emits
        it once rather than letting rule induction and rule application each derive
        their own version and drift apart.

        ``None`` when the books expected no fee at all: a percentage of zero is
        undefined, and reporting it as 0.00% would read as "no variance" on exactly
        the rows that have the largest one.
        """
        if self.expected_fee == ZERO:
            return None
        return (self.fee_delta / self.expected_fee * HUNDRED).quantize(PCT)

    @property
    def net_variance_pct(self) -> Decimal | None:
        """Net delta as a percentage of expected net. Negative means short-paid."""
        if self.expected_net == ZERO:
            return None
        return (self.net_delta / self.expected_net * HUNDRED).quantize(PCT)

    @property
    def impact(self) -> Decimal:
        """Rupees the variance puts in question. Zero on a clean match."""
        if self.bucket is Bucket.MATCHED:
            return ZERO
        return max(abs(self.fee_delta), abs(self.net_delta))


def check_value(ledger: LedgerRow, rows: list[NormalisedRow], cfg: MatchConfig) -> ValueCheck:
    """Compare one order's settlement rows against the ledger's expectation."""
    charged = charged_fee(rows)
    settled = total_net(rows)
    tolerance = cfg.value_tolerance(ledger.expected_fee)

    fee_off = abs(charged - ledger.expected_fee) > tolerance
    net_off = abs(settled - ledger.expected_net) > tolerance
    held = any(row.row.on_hold for row in rows)
    # Books that expect nothing at all from an order have already written it off.
    reversed_in_books = ledger.expected_net == ZERO and ledger.expected_fee == ZERO

    if held and (fee_off or net_off):
        # The platform reported the sale and paid nothing out pending a dispute.
        # Held is not lost, and calling it a plain shortfall would misroute it.
        reason = Reason.PAYMENT_WITHHELD_ON_HOLD
    elif reversed_in_books and net_off:
        # "Fee variance of 813.24 against an expected fee of 0.00" is arithmetically
        # true and useless to read. The finding is that the books wrote this order
        # off and the platform paid it anyway -- the deduction is still to come.
        reason = Reason.PAID_AGAINST_REVERSED_ORDER
    elif fee_off:
        reason = Reason.FEE_OUTSIDE_TOLERANCE
    elif net_off:
        reason = Reason.NET_OUTSIDE_TOLERANCE
    else:
        reason = Reason.ORDER_MATCHED_CLEAN

    return ValueCheck(
        bucket=Bucket.MATCHED if reason is Reason.ORDER_MATCHED_CLEAN else Bucket.VARIANCE,
        reason=reason,
        tolerance=tolerance,
        expected_fee=ledger.expected_fee,
        charged_fee=charged,
        expected_net=ledger.expected_net,
        settled_net=settled,
    )
