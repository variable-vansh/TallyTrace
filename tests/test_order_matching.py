"""Order-level matching, the date window, and value-level variance.

Hand-built ledger and settlement rows throughout. The tolerance band is derived
from the ledger's own ``expected_commission_rate``, so these tests assert the band
*moves with the order* rather than asserting a rupee constant -- which is the whole
reason the band is derived rather than configured flat.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pipeline.matcher.normalise import normalise_all
from pipeline.matcher.orders import OpenOrder, due_by, match_orders, settlement_delay_days
from pipeline.matcher.reasons import Bucket, Reason
from pipeline.matcher.settings import MatchConfig
from pipeline.matcher.variance import check_value
from pipeline.models import Channel, LedgerRow, SettlementRow, TransactionType

D = Decimal
CFG = MatchConfig(D("1.00"), 21, D("0.5"), 3, 60)
BATCH_END = date(2025, 1, 12)


def ledger(fee: str = "500.00", net: str = "1500.00", rate: str = "0.25") -> LedgerRow:
    return LedgerRow(
        order_id="ord_000001", channel=Channel.MYNTRA, order_value=D("2000.00"),
        expected_commission_rate=D(rate), expected_fee=D(fee), expected_net=D(net),
    )


def settlement(fee: str = "500.00", net: str = "1500.00", **overrides: object) -> SettlementRow:
    fields: dict[str, object] = dict(
        entity_id="st_000001", type=TransactionType.PAYMENT, channel=Channel.MYNTRA,
        order_id="ord_000001", amount=D("2000.00"), fee=D(fee), tax=D("0.00"),
        tcs=D("0.00"), tds=D("0.00"), debit=D("0.00"), credit=D(net),
        settlement_id="MYN-PO-1", settlement_utr="ICICN1",
        created_at=date(2025, 1, 1), settled_at=date(2025, 1, 10),
    )
    fields.update(overrides)
    return SettlementRow(**fields)  # type: ignore[arg-type]


def open_order(row: LedgerRow | None = None, batch: int = 1) -> OpenOrder:
    return OpenOrder(ledger=row or ledger(), booked_batch=batch, booked_window_end=BATCH_END)


def find(rows: list[SettlementRow], order: OpenOrder | None = None, cfg: MatchConfig = CFG,
         batch_end: date = BATCH_END):
    order = order or open_order()
    by_order = {order.order_id: normalise_all(rows)} if rows else {}
    return match_orders([order], by_order, batch_end, cfg)[0]


# --------------------------------------------------------------------------- #
# Value level
# --------------------------------------------------------------------------- #


def test_a_settlement_matching_the_books_is_clean() -> None:
    assert find([settlement()]).reason is Reason.ORDER_MATCHED_CLEAN


def test_paise_drift_stays_inside_the_tolerance_floor() -> None:
    """Rounding variance should cost nobody any attention."""
    assert find([settlement(fee="500.99", net="1499.01")]).bucket is Bucket.MATCHED


def test_a_stale_commission_rate_fires_a_fee_variance() -> None:
    """25% booked, 27.2% charged on a ₹2000 order: ₹44 against a ₹2.50 band."""
    finding = find([settlement(fee="544.00", net="1456.00")])
    assert finding.bucket is Bucket.VARIANCE
    assert finding.reason is Reason.FEE_OUTSIDE_TOLERANCE
    assert finding.impact == D("44.00")


def test_the_tolerance_band_scales_with_the_ledgers_own_expectation() -> None:
    """A ₹4 gap clears a gateway's 2% band and does not clear a marketplace's 25%."""
    small = check_value(ledger(fee="40.00", net="1960.00"), normalise_all([settlement(
        fee="44.00", net="1956.00")]), CFG)
    large = check_value(ledger(fee="500.00"), normalise_all([settlement(
        fee="504.00", net="1496.00")]), CFG)
    assert small.tolerance == D("1.00") and small.bucket is Bucket.VARIANCE
    assert large.tolerance == D("2.50") and large.bucket is Bucket.VARIANCE
    assert large.tolerance > small.tolerance


def test_a_net_short_with_the_right_fee_fires_a_net_variance() -> None:
    finding = find([settlement(net="1300.00")])
    assert finding.reason is Reason.NET_OUTSIDE_TOLERANCE
    assert finding.impact == D("200.00")


def test_a_dispute_hold_is_named_as_a_hold_not_as_a_shortfall() -> None:
    """Held is not lost. Calling it a plain shortfall would misroute the claim."""
    finding = find([settlement(net="0.00", on_hold=True)])
    assert finding.reason is Reason.PAYMENT_WITHHELD_ON_HOLD
    assert finding.bucket is Bucket.VARIANCE


def test_a_payment_and_its_refund_in_one_batch_net_to_the_books_expectation() -> None:
    """A fully reversed order expects nothing, and gets nothing."""
    rows = [
        settlement(),
        settlement(entity_id="st_000002", type=TransactionType.REFUND, amount=D("-2000.00"),
                   fee=D("-500.00"), credit=D("-1500.00")),
    ]
    assert find(rows, open_order(ledger(fee="0.00", net="0.00"))).bucket is Bucket.MATCHED


# --------------------------------------------------------------------------- #
# Date window
# --------------------------------------------------------------------------- #


def test_a_settlement_inside_the_window_is_not_late() -> None:
    rows = normalise_all([settlement(settled_at=date(2025, 1, 22))])
    assert settlement_delay_days(rows, CFG) == 0, "21 days is the configured window, not late"


def test_a_settlement_past_the_window_is_a_variance_even_when_the_money_is_right() -> None:
    """Cross-batch lag: correct to the paise, and the cash was not where the books said."""
    finding = find([settlement(settled_at=date(2025, 1, 29))])
    assert finding.bucket is Bucket.VARIANCE
    assert finding.reason is Reason.SETTLEMENT_OUTSIDE_DATE_WINDOW
    assert finding.days_late == 7


def test_a_money_variance_outranks_a_late_arrival() -> None:
    """Both are true; the rupee finding is the one a bookkeeper acts on."""
    finding = find([settlement(fee="544.00", net="1456.00", settled_at=date(2025, 1, 29))])
    assert finding.reason is Reason.FEE_OUTSIDE_TOLERANCE
    assert finding.days_late == 7, "the delay is still recorded, just not the headline"


def test_an_unsettled_order_inside_the_window_is_carried_not_flagged() -> None:
    """This is what stops normal settlement lag reading as lost money."""
    finding = find([])
    assert finding.reason is Reason.AWAITING_SETTLEMENT_IN_WINDOW
    assert finding.impact == D("0.00"), "nothing is owed yet, so nothing is at stake yet"


def test_an_unsettled_order_past_the_window_becomes_an_exception_worth_its_net() -> None:
    finding = find([], batch_end=date(2025, 2, 10))
    assert finding.reason is Reason.SETTLEMENT_OVERDUE
    assert finding.impact == D("1500.00")


def test_the_window_is_measured_from_the_end_of_the_booking_batch() -> None:
    assert due_by(open_order(), CFG) == date(2025, 2, 2)


# --------------------------------------------------------------------------- #
# The band a rule is written in
# --------------------------------------------------------------------------- #


def test_the_fee_variance_percentage_is_the_band_a_commission_rule_uses() -> None:
    """25% booked, 27.2% charged: 8.8% over, which is what the rule says."""
    check = check_value(ledger(fee="500.00"), normalise_all([settlement(
        fee="544.00", net="1456.00")]), CFG)
    assert check.fee_variance_pct == D("8.80")


def test_the_net_variance_percentage_is_negative_when_the_seller_is_short() -> None:
    check = check_value(ledger(), normalise_all([settlement(net="1300.00")]), CFG)
    assert check.net_variance_pct == D("-13.33"), "short-paid reads negative"


def test_a_percentage_of_a_zero_expectation_is_undefined_not_zero() -> None:
    """The books wrote this order off. 0.00% would read as 'no variance' on the row
    that has the largest one."""
    check = check_value(ledger(fee="0.00", net="0.00"), normalise_all([settlement()]), CFG)
    assert check.fee_variance_pct is None
    assert check.net_variance_pct is None
    assert check.reason is Reason.PAID_AGAINST_REVERSED_ORDER
