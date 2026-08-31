"""The rules Claude induces from each operator note.

Authored the same way as the hypotheses: by reading the rendered induction prompt --
the note plus the row's shape -- and returning the schema the client asks for. The
answer key was not open. Two of these are deliberately faithful to notes that
over-generalise, because the note is the input and correcting it here would be
inventing a bookkeeper who does not exist.

Keyed by the question signature the note was written against, plus the batch for the
one note the operator revised. ``tools/write_llm_fixtures.py`` walks the real
resolution log, renders the real prompt for each, and writes one cache entry per
distinct prompt -- so a note applied at two different money magnitudes produces two
cache entries carrying the same rule, which is what the live client would do too.
"""

from __future__ import annotations

from typing import Any

FEE_VAR = "fee_variance_outside_tolerance"
NET_VAR = "net_variance_outside_tolerance"
LATE_ROW = "late_row_for_already_settled_order"
OUTSIDE_WINDOW = "settlement_outside_date_window"
OVERDUE = "settlement_overdue_beyond_window"


def rule(**fields: Any) -> dict[str, Any]:
    """One induced rule, with the schema's defaults spelled out."""
    payload: dict[str, Any] = {
        "channel": None,
        "cause": fields["cause"],
        "reason_code": fields.get("reason_code"),
        "transaction_type": fields.get("transaction_type"),
        "variance_band_pct": fields.get("variance_band_pct"),
        "net_variance_band_pct": fields.get("net_variance_band_pct"),
        "direction": fields.get("direction", "any"),
        "lag_window_days": fields.get("lag_window_days"),
        "resolution_class": fields["resolution_class"],
        "action": {
            "type": fields.get("action", "none"),
            "field": fields.get("action_field"),
            "value": fields.get("action_value"),
        },
        "plain_words": fields["plain_words"],
    }
    payload["channel"] = fields.get("channel")
    return payload


RULES: dict[tuple, dict[str, Any]] = {
    # -- stale commission rates ---------------------------------------------- #
    (FEE_VAR, "myntra", "short", "8.8", "-3.7", None, None, None): rule(
        channel="myntra", cause="commission_rate_stale", reason_code=FEE_VAR,
        variance_band_pct=[8.0, 9.5], direction="short", resolution_class="internal_fix",
        action="update_ledger_rate", action_field="expected_commission_rate", action_value=0.272,
        plain_words="Myntra bills commission at 27.2% while the master rate sheet still says "
                    "25%, so the fee runs about 8.8% over expectation on every order.",
    ),
    (FEE_VAR, "amazon", "short", "7.5", "-2.4", None, None, None): rule(
        channel="amazon", cause="commission_rate_stale", reason_code=FEE_VAR,
        variance_band_pct=[6.8, 8.2], direction="short", resolution_class="internal_fix",
        action="update_ledger_rate", action_field="expected_commission_rate", action_value=0.215,
        plain_words="Amazon has charged 21.5% referral fee since the revision while the master "
                    "rate sheet still says 20%, leaving the fee about 7.5% over expectation.",
    ),
    # -- slab changes --------------------------------------------------------- #
    (FEE_VAR, "flipkart", "short", "41.2", "-10.5", None, None, None): rule(
        channel="flipkart", cause="commission_slab_change", reason_code=FEE_VAR,
        variance_band_pct=[20.0, 45.0], direction="short", resolution_class="internal_fix",
        plain_words="Flipkart has moved individual categories into a higher commission slab "
                    "than the one the order was priced against.",
    ),
    # -- other fee components -------------------------------------------------- #
    (FEE_VAR, "website", "short", "74.8", "-1.8", None, None, None): rule(
        channel="website", cause="fee_mismatch_other", reason_code=FEE_VAR,
        variance_band_pct=[50.0, 120.0], net_variance_band_pct=[-3.0, 0.0], direction="short",
        resolution_class="internal_fix", action="write_off_variance",
        plain_words="The website gateway charges more than the flat 2% the books model on some "
                    "payment instruments; the rupee difference is small enough to write off.",
    ),
    (FEE_VAR, "offline", "short", "129.9", "-3.1", None, None, None): rule(
        channel="offline", cause="fee_mismatch_other", reason_code=FEE_VAR,
        variance_band_pct=[100.0, 160.0], net_variance_band_pct=[-4.0, 0.0], direction="short",
        resolution_class="internal_fix", action="write_off_variance",
        plain_words="Card-present pricing at the counter is above the flat 2% the POS fee model "
                    "assumes; small enough to write off.",
    ),
    # -- promotional co-funding ------------------------------------------------ #
    (FEE_VAR, "myntra", "short", "40.0", "-10.6", None, None, None): rule(
        channel="myntra", cause="promo_cofunding_deduction", reason_code=FEE_VAR,
        variance_band_pct=[25.0, 45.0], direction="short",
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="Myntra has billed a share of a campaign cost as commission, varying order "
                    "to order and far above the agreed rate; it needs itemising before it is accepted.",
    ),
    (FEE_VAR, "amazon", "short", "30.0", "-8.0", None, None, None): rule(
        channel="amazon", cause="promo_cofunding_deduction", reason_code=FEE_VAR,
        variance_band_pct=[20.0, 40.0], direction="short",
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="Amazon has deducted a promotional cost on top of the referral fee without "
                    "notice; the amount varies per order, so it is not a rate change.",
    ),
    # -- refund and reversal timing -------------------------------------------- #
    ("paid_against_fully_reversed_order", "amazon", "over", None, None, None, None, None): rule(
        channel="amazon", cause="refund_timing_lag", reason_code="paid_against_fully_reversed_order",
        direction="over", resolution_class="internal_fix", action="accept_timing_difference",
        plain_words="The books have already written the order down to zero for a refund while the "
                    "platform has paid it in full; its deduction lands in a later settlement.",
    ),
    (FEE_VAR, "amazon", "over", "-100.0", "0.0", None, "8-14", None): rule(
        channel="amazon", cause="refund_timing_lag", reason_code=FEE_VAR,
        variance_band_pct=[-100.0, -99.0], direction="over", resolution_class="internal_fix",
        action="accept_timing_difference",
        plain_words="A sale and its reversal have both landed, so no commission is left on the "
                    "order and the net ties; only the cycle they fell in differs from the books.",
    ),
    (FEE_VAR, "myntra", "over", "-100.0", "0.0", None, "8-14", None): rule(
        channel="myntra", cause="refund_timing_lag", reason_code=FEE_VAR,
        variance_band_pct=[-100.0, -99.0], direction="over", resolution_class="internal_fix",
        action="accept_timing_difference",
        plain_words="A reversal has caught up with its sale a cycle later; the net ties and no "
                    "commission remains on the order.",
    ),
    # -- the loose one, faithful to a note that generalised across channels ----- #
    (LATE_ROW, "flipkart", "short", None, None, "1-7", None, "refund"): rule(
        cause="rto_reversal_later_cycle", reason_code=LATE_ROW, transaction_type="refund",
        direction="short", lag_window_days=[1, 21], resolution_class="internal_fix",
        action="accept_timing_difference",
        plain_words="A deduction arriving within three weeks of an order settling is the return "
                    "coming back through, and nets off against the original sale.",
    ),
    (LATE_ROW, "flipkart", "short", None, None, "8-14", None, "refund"): rule(
        channel="flipkart", cause="rto_reversal_later_cycle", reason_code=LATE_ROW,
        transaction_type="refund", direction="short", lag_window_days=[1, 21],
        resolution_class="internal_fix", action="accept_timing_difference",
        plain_words="On Flipkart a deduction against an order that settled at full value with no "
                    "refund on the seller's side is a returned shipment, landing two to three "
                    "weeks after the sale.",
    ),
    (LATE_ROW, "amazon", "short", None, None, "8-14", None, "refund"): rule(
        channel="amazon", cause="refund_timing_lag", reason_code=LATE_ROW,
        transaction_type="refund", direction="short", lag_window_days=[1, 14],
        resolution_class="internal_fix", action="accept_timing_difference",
        plain_words="On Amazon a deduction after an order has settled is a refund the seller "
                    "already booked, taken on the platform's next cycle rather than a return.",
    ),
    # -- tax timing ------------------------------------------------------------ #
    (LATE_ROW, "myntra", "short", None, None, "1-7", None, "adjustment"): rule(
        channel="myntra", cause="tcs_timing_mismatch", reason_code=LATE_ROW,
        transaction_type="adjustment", direction="short", lag_window_days=[1, 7],
        resolution_class="tax_review",
        plain_words="Tax collected at source recovered on a later settlement than the sale it "
                    "belongs to; it needs a tax review rather than a reconciliation fix.",
    ),
    (LATE_ROW, "flipkart", "short", None, None, "1-7", None, "adjustment"): rule(
        channel="flipkart", cause="tcs_timing_mismatch", reason_code=LATE_ROW,
        transaction_type="adjustment", direction="short", lag_window_days=[1, 7],
        resolution_class="tax_review",
        plain_words="Tax collected at source picked up a cycle after the sale; it belongs in the "
                    "tax review pile.",
    ),
    (LATE_ROW, "amazon", "short", None, None, "1-7", None, "adjustment"): rule(
        channel="amazon", cause="tds_timing_mismatch", reason_code=LATE_ROW,
        transaction_type="adjustment", direction="short", lag_window_days=[1, 7],
        resolution_class="tax_review",
        plain_words="Withholding under 194-O deducted later than the payment it belongs to; a tax "
                    "review, not a reconciliation difference.",
    ),
    # -- claims ---------------------------------------------------------------- #
    ("payment_withheld_on_hold", "flipkart", "short", "0.0", "-100.0", None, None, None): rule(
        channel="flipkart", cause="weight_dispute_hold", reason_code="payment_withheld_on_hold",
        net_variance_band_pct=[-100.0, -99.0], direction="short",
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="Flipkart has taken commission at the expected rate and withheld the payout "
                    "pending a parcel weight dispute; the money is held, not lost.",
    ),
    (NET_VAR, "flipkart", "short", "0.0", "-12.0", None, None, None): rule(
        channel="flipkart", cause="short_payment_unexplained", reason_code=NET_VAR,
        net_variance_band_pct=[-13.0, -11.0], direction="short",
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="Commission is exactly right and the payout is still around 12% light with "
                    "no deduction line explaining it; the platform owes the difference.",
    ),
    (NET_VAR, "myntra", "short", "0.0", "-10.0", None, None, None): rule(
        channel="myntra", cause="short_payment_unexplained", reason_code=NET_VAR,
        net_variance_band_pct=[-11.0, -9.0], direction="short",
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="The fee matches the books and the payout is about 10% short with nothing "
                    "itemised; it goes back to the platform rather than into the seller's books.",
    ),
    (NET_VAR, "website", "short", "0.0", "-100.0", None, None, None): rule(
        channel="website", cause="chargeback_deduction", reason_code=NET_VAR,
        net_variance_band_pct=[-100.0, -99.0], direction="short",
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="The whole payment was pulled back on a card-acquired website order while the "
                    "gateway kept its fee, which is a cardholder dispute to contest, not a refund.",
    ),
    (OVERDUE, "amazon", "flat", None, None, None, None, None): rule(
        channel="amazon", cause="missing_settlement_row", reason_code=OVERDUE,
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="The settlement window has closed with no line for the order anywhere in the "
                    "platform's report; it is unpaid rather than late and needs a claim.",
    ),
    (OVERDUE, "myntra", "flat", None, None, None, None, None): rule(
        channel="myntra", cause="missing_settlement_row", reason_code=OVERDUE,
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="Booked on the seller's side, past the settlement window and absent from the "
                    "platform's report; chase it as a missing payment.",
    ),
    (LATE_ROW, "flipkart", "over", None, None, "8-14", None, "adjustment"): rule(
        channel="flipkart", cause="short_payment_unexplained", reason_code=LATE_ROW,
        transaction_type="adjustment", direction="over", lag_window_days=[8, 14],
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="An unreferenced credit arriving against an order that closed weeks earlier "
                    "looks like settlement of a shortfall already raised; keep the claim open "
                    "until the platform confirms which one.",
    ),
    (LATE_ROW, "myntra", "over", None, None, "22+", None, "adjustment"): rule(
        channel="myntra", cause="missing_settlement_row", reason_code=LATE_ROW,
        transaction_type="adjustment", direction="over", lag_window_days=[22, 90],
        resolution_class="counterparty_claim", action="flag_for_claim",
        plain_words="A credit arriving more than three weeks late for an order the platform never "
                    "reported at the time; keep the claim open until it is matched.",
    ),
    # -- settlement timing ----------------------------------------------------- #
    (OUTSIDE_WINDOW, "amazon", "flat", "0.0", "0.0", None, "8-14", None): rule(
        channel="amazon", cause="settlement_lag_crossing_batch", reason_code=OUTSIDE_WINDOW,
        variance_band_pct=[-1.0, 1.0], direction="flat", resolution_class="internal_fix",
        action="accept_timing_difference",
        plain_words="A payout correct to the paise that landed after the settlement window, so "
                    "the sale and its payment fall in different reporting weeks.",
    ),
    # -- duplicated report lines ------------------------------------------------ #
    ("bank_group_sum_mismatch", None, "short", None, None, None, None, None): rule(
        cause="duplicate_settlement_row", reason_code="bank_group_sum_mismatch",
        direction="short", resolution_class="internal_fix",
        plain_words="A payout group that adds up to more than the credit funding it usually "
                    "contains a transaction the report emitted twice; the bank is right.",
    ),
    ("not_funded_by_bank_credit", "flipkart", "over", None, None, None, None, None): rule(
        channel="flipkart", cause="duplicate_settlement_row", reason_code="not_funded_by_bank_credit",
        direction="over", resolution_class="internal_fix",
        plain_words="A settlement line the payout did not fund, where the credit covers every "
                    "other row exactly, is a re-emission of a transaction already paid once.",
    ),
    ("bank_credit_without_settlement_group", None, "over", None, None, None, None, None): rule(
        cause="bank_credit_unmatched", reason_code="bank_credit_without_settlement_group",
        direction="over", resolution_class="investigate",
        plain_words="Money in the account with no settlement report referencing the bank "
                    "reference; it cannot be allocated until the remitter is identified.",
    ),
    # -- small write-offs -------------------------------------------------------- #
    (NET_VAR, "amazon", "over", "0.0", "0.1", None, None, None): rule(
        channel="amazon", cause="rounding_variance", reason_code=NET_VAR,
        net_variance_band_pct=[-0.5, 0.5], direction="over", resolution_class="internal_fix",
        action="write_off_variance",
        plain_words="A few paise over with commission exactly right is tax rounding, not a "
                    "difference worth anyone's attention.",
    ),
    (NET_VAR, "myntra", "over", "0.0", "1.4", None, None, None): rule(
        channel="myntra", cause="fee_mismatch_other", reason_code=NET_VAR,
        net_variance_band_pct=[1.0, 2.0], direction="over", resolution_class="internal_fix",
        action="write_off_variance",
        plain_words="Slightly more paid than expected with commission correct, because a shipping "
                    "charge the books budget for was not billed; small enough to write off.",
    ),
    (NET_VAR, "flipkart", "over", "0.0", "1.3", None, None, None): rule(
        channel="flipkart", cause="fee_mismatch_other", reason_code=NET_VAR,
        net_variance_band_pct=[1.0, 2.0], direction="over", resolution_class="internal_fix",
        action="write_off_variance",
        plain_words="A small overpayment with correct commission, from a fulfilment charge the "
                    "books model but the platform did not bill.",
    ),
}

#: Notes whose induced rule is the same as another note's. The operator writes a
#: fresh sentence about the same phenomenon at a different lag or a different
#: percentage; the rule it implies is identical, and the store collapses them.
ALIASES: dict[tuple, tuple] = {
    (FEE_VAR, "website", "short", "91.1", "-2.2", None, None, None):
        (FEE_VAR, "website", "short", "74.8", "-1.8", None, None, None),
    (FEE_VAR, "flipkart", "short", "26.3", "-7.7", None, None, None):
        (FEE_VAR, "flipkart", "short", "41.2", "-10.5", None, None, None),
    (FEE_VAR, "flipkart", "short", "22.0", "-7.8", None, None, None):
        (FEE_VAR, "flipkart", "short", "41.2", "-10.5", None, None, None),
    (FEE_VAR, "myntra", "short", "32.0", "-11.5", None, None, None):
        (FEE_VAR, "myntra", "short", "40.0", "-10.6", None, None, None),
    (FEE_VAR, "myntra", "short", "28.6", "-12.2", None, None, None):
        (FEE_VAR, "myntra", "short", "40.0", "-10.6", None, None, None),
    (FEE_VAR, "amazon", "short", "25.0", "-8.5", None, None, None):
        (FEE_VAR, "amazon", "short", "30.0", "-8.0", None, None, None),
    (FEE_VAR, "amazon", "short", "27.3", "-8.2", None, None, None):
        (FEE_VAR, "amazon", "short", "30.0", "-8.0", None, None, None),
    (FEE_VAR, "amazon", "short", "33.3", "-7.7", None, None, None):
        (FEE_VAR, "amazon", "short", "30.0", "-8.0", None, None, None),
    (FEE_VAR, "amazon", "over", "-100.0", "0.0", None, "1-7", None):
        (FEE_VAR, "amazon", "over", "-100.0", "0.0", None, "8-14", None),
    (LATE_ROW, "flipkart", "short", None, None, "15-21", None, "refund"):
        (LATE_ROW, "flipkart", "short", None, None, "8-14", None, "refund"),
    (LATE_ROW, "amazon", "short", None, None, "1-7", None, "refund"):
        (LATE_ROW, "amazon", "short", None, None, "8-14", None, "refund"),
    (OUTSIDE_WINDOW, "amazon", "flat", "0.0", "0.0", None, "1-7", None):
        (OUTSIDE_WINDOW, "amazon", "flat", "0.0", "0.0", None, "8-14", None),
    ("not_funded_by_bank_credit", "myntra", "over", None, None, None, None, None):
        ("not_funded_by_bank_credit", "flipkart", "over", None, None, None, None, None),
    ("not_funded_by_bank_credit", "amazon", "over", None, None, None, None, None):
        ("not_funded_by_bank_credit", "flipkart", "over", None, None, None, None, None),
}

#: The operator's revised note in batch 4. It says the same thing the batch-3
#: Flipkart note says, which is the point: the correction converges on the rule that
#: already exists rather than adding another.
OVERRIDE_RULES: dict[tuple, tuple] = {
    (4, (LATE_ROW, "flipkart", "short", None, None, "1-7", None, "refund")):
        (LATE_ROW, "flipkart", "short", None, None, "8-14", None, "refund"),
}


def resolve(signature: tuple) -> dict[str, Any] | None:
    """The rule for a note, following aliases."""
    target = ALIASES.get(signature, signature)
    return RULES.get(target)
