"""Reason codes.

Every verdict carries one. The code is the machine-readable answer to "which check
produced this?", and it is the join key for everything downstream: checkpoint 3
induces rules from it, the harness builds its confusion table from it, the UI shows
it. So it is a frozen enum rather than a free-text string -- a reason code that
drifts is a learning loop that silently stops learning.

Reason codes are *not* causes. A cause is what the ground truth says was done to the
data; a reason is what the matcher observed. Keeping them separate is what lets the
harness ask "which bucket did cause X land in?" and get a real answer.
"""

from __future__ import annotations

from enum import Enum


class Bucket(str, Enum):
    """The four output buckets. Every input row lands in exactly one."""

    MATCHED = "matched"
    VARIANCE = "variance"
    UNMATCHED = "unmatched"
    QUARANTINED = "quarantined"


class Reason(str, Enum):
    """Why a row landed where it did."""

    # -- clean ------------------------------------------------------------- #
    ORDER_MATCHED_CLEAN = "order_matched_clean"
    BANK_GROUP_TIES_OUT = "bank_group_ties_out"

    # -- value-level variance ---------------------------------------------- #
    FEE_OUTSIDE_TOLERANCE = "fee_variance_outside_tolerance"
    NET_OUTSIDE_TOLERANCE = "net_variance_outside_tolerance"
    PAYMENT_WITHHELD_ON_HOLD = "payment_withheld_on_hold"
    PAID_AGAINST_REVERSED_ORDER = "paid_against_fully_reversed_order"

    # -- order level -------------------------------------------------------- #
    AWAITING_SETTLEMENT_IN_WINDOW = "awaiting_settlement_in_window"
    SETTLEMENT_OUTSIDE_DATE_WINDOW = "settlement_outside_date_window"
    SETTLEMENT_OVERDUE = "settlement_overdue_beyond_window"
    ROW_FOR_UNKNOWN_ORDER = "settlement_row_for_unknown_order"
    LATE_ROW_FOR_SETTLED_ORDER = "late_row_for_already_settled_order"
    ADJUSTMENT_WITHOUT_ORDER = "adjustment_without_order_reference"

    # -- bank level --------------------------------------------------------- #
    NOT_FUNDED_BY_BANK_CREDIT = "not_funded_by_bank_credit"
    SETTLEMENT_GROUP_NO_BANK_CREDIT = "settlement_group_without_bank_credit"
    BANK_GROUP_SUM_MISMATCH = "bank_group_sum_mismatch"
    BANK_CREDIT_NO_SETTLEMENT_GROUP = "bank_credit_without_settlement_group"
    BANK_CREDIT_REVERSED = "bank_credit_reversed"

    # -- quarantine --------------------------------------------------------- #
    MALFORMED_MISSING_ORDER_ID = "malformed_missing_order_id"
    MALFORMED_UNPARSEABLE_DATE = "malformed_unparseable_date"
    MALFORMED_UNPARSEABLE_AMOUNT = "malformed_unparseable_amount"
    MALFORMED_SCHEMA_VIOLATION = "malformed_schema_violation"


#: Bucket precedence, least to most severe. Used where two checks both have an
#: opinion about the same row: the more severe verdict wins and the other is
#: recorded in the verdict detail rather than thrown away.
SEVERITY: dict[Bucket, int] = {
    Bucket.MATCHED: 0,
    Bucket.VARIANCE: 1,
    Bucket.UNMATCHED: 2,
    Bucket.QUARANTINED: 3,
}
