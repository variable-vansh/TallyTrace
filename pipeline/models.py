"""The data contract.

Every later module codes against these models. They live under ``pipeline/`` because
the pipeline is their principal consumer; the generator imports them so that the
synthetic world cannot drift from the shape the matcher expects.

All money is ``decimal.Decimal``. A float in a money path is a bug, so the money
validator rejects floats outright rather than silently coercing them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

# --------------------------------------------------------------------------- #
# Money
# --------------------------------------------------------------------------- #

PAISE = Decimal("0.01")


def _to_decimal(value: Any) -> Decimal:
    """Coerce to Decimal, refusing floats.

    Floats are refused rather than converted: accepting one here is how binary
    rounding error gets into a reconciliation and then into a claim.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"float is not allowed in a money field (got {value!r}); pass Decimal or str")
    if isinstance(value, (int, str)):
        try:
            return Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValueError(f"not a decimal amount: {value!r}") from exc
    raise ValueError(f"unsupported money value: {value!r}")


Money = Annotated[Decimal, BeforeValidator(_to_decimal)]
Rate = Annotated[Decimal, BeforeValidator(_to_decimal)]


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #


class TransactionType(str, Enum):
    PAYMENT = "payment"
    REFUND = "refund"
    TRANSFER = "transfer"
    ADJUSTMENT = "adjustment"


class Channel(str, Enum):
    AMAZON = "amazon"
    FLIPKART = "flipkart"
    MYNTRA = "myntra"
    WEBSITE = "website"
    OFFLINE = "offline"


class BankStatus(str, Enum):
    PROCESSED = "processed"
    REVERSED = "reversed"


class LedgerStatus(str, Enum):
    BOOKED = "booked"
    MATCHED = "matched"
    EXCEPTION = "exception"
    RESOLVED = "resolved"
    WRITTEN_OFF = "written_off"
    CLAIMED = "claimed"


class ResolutionClass(str, Enum):
    INTERNAL_FIX = "internal_fix"
    COUNTERPARTY_CLAIM = "counterparty_claim"
    TAX_REVIEW = "tax_review"
    INVESTIGATE = "investigate"


class Cause(str, Enum):
    """The frozen enum. Mirrors ``config/causes.yaml``; a test asserts they agree."""

    COMMISSION_RATE_STALE = "commission_rate_stale"
    COMMISSION_SLAB_CHANGE = "commission_slab_change"
    FEE_MISMATCH_OTHER = "fee_mismatch_other"
    RTO_REVERSAL_LATER_CYCLE = "rto_reversal_later_cycle"
    REFUND_TIMING_LAG = "refund_timing_lag"
    SETTLEMENT_LAG_CROSSING_BATCH = "settlement_lag_crossing_batch"
    ROUNDING_VARIANCE = "rounding_variance"
    DUPLICATE_SETTLEMENT_ROW = "duplicate_settlement_row"
    TCS_TIMING_MISMATCH = "tcs_timing_mismatch"
    TDS_TIMING_MISMATCH = "tds_timing_mismatch"
    WEIGHT_DISPUTE_HOLD = "weight_dispute_hold"
    MISSING_SETTLEMENT_ROW = "missing_settlement_row"
    SHORT_PAYMENT_UNEXPLAINED = "short_payment_unexplained"
    CHARGEBACK_DEDUCTION = "chargeback_deduction"
    PROMO_COFUNDING_DEDUCTION = "promo_cofunding_deduction"
    BANK_CREDIT_UNMATCHED = "bank_credit_unmatched"


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, use_enum_values=False)


class SettlementRow(_Row):
    """One row of a platform / gateway settlement report."""

    entity_id: str
    type: TransactionType
    channel: Channel
    order_id: str | None = None          # nullable for `adjustment` rows
    amount: Money                        # gross, signed
    fee: Money                           # platform + fulfilment fee
    tax: Money                           # GST on fee
    tcs: Money                           # 1% collected at source
    tds: Money                           # 0.1% under 194-O
    debit: Money
    credit: Money
    settlement_id: str
    settlement_utr: str
    created_at: date                     # transaction date
    settled_at: date                     # payout date; may fall in a later batch
    on_hold: bool = False
    dispute_id: str | None = None
    description: str = ""

    @model_validator(mode="after")
    def _order_id_required_except_on_adjustments(self) -> "SettlementRow":
        """Only an adjustment may arrive without an order id.

        A payment or a refund with no order id cannot be joined to anything, so it
        is malformed input and belongs in quarantine, not in the matcher.
        """
        if self.type is not TransactionType.ADJUSTMENT and not self.order_id:
            raise ValueError(f"{self.type.value} row {self.entity_id} has no order_id")
        return self


class BankRow(_Row):
    """One credit landing in the single bank account."""

    utr: str
    amount: Money
    created_at: date
    status: BankStatus = BankStatus.PROCESSED


class LedgerRow(_Row):
    """One order in the seller's own books."""

    order_id: str
    channel: Channel
    order_value: Money
    expected_commission_rate: Rate       # config, and it can go stale. Deliberate.
    expected_fee: Money
    expected_net: Money
    status: LedgerStatus = LedgerStatus.BOOKED
    resolution_reason: str | None = None


class Batch(_Row):
    """The three tables for one weekly batch."""

    batch: int = Field(ge=1)
    settlements: list[SettlementRow]
    bank: list[BankRow]
    ledger: list[LedgerRow]
