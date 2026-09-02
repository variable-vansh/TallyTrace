"""Write the LLM response cache from authored fixtures.

**Provenance, stated plainly.** The entries this script writes were produced by
Claude Opus reading each rendered prompt and answering it, through a coding session
rather than through the HTTP Messages API. The request text and the output schema
are byte-identical to what ``pipeline/llm/client.py`` would send; what differs is
the transport. Every entry is therefore written with ``source: "transcript"``, and
that field is what makes the harness label its token counts *estimated* rather than
metered. Nothing downstream is allowed to quietly treat one as the other.

Set ``ANTHROPIC_API_KEY`` and delete ``data/llm_cache/`` and the same prompts go
over the wire instead; the cache repopulates with ``source: "api"`` and metered
usage, and no other code changes.

The answers below were written against the rendered prompt and nothing else. The
answer key in ``data/truth`` was not open while they were written, and it must not
be: an oracle-informed hypothesis makes the auto-resolution precision number
meaningless, which is the one number checkpoint 3 is graded on.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from pipeline.cases import FindingLog, build_cases
from pipeline.config import CONFIG_DIR, load_yaml
from pipeline.llm.cache import SOURCE_TRANSCRIPT, CacheEntry, ResponseCache, estimate_tokens, key_for
from pipeline.llm.client import Ask
from pipeline.llm.drafts import DraftQuestion, ask_for as draft_ask
from pipeline.llm.intent import ask_for as intent_ask
from pipeline.llm.hypotheses import Question, ask_for as hypothesis_ask, questions_in
from pipeline.llm.induction import ask_for as induction_ask
from pipeline.llm.schemas import ClaimNarrative, Hypothesis, InducedRule, MetricIntent, json_schema
from pipeline.matcher import Bucket
from pipeline.run import run_all

# (cause, hypothesis, confidence) keyed by the question's signature tuple.
HYPOTHESES: dict[tuple, tuple[str, str, str]] = {
    # -- bank level ---------------------------------------------------------- #
    ("bank_group_sum_mismatch", None, "short", None, None, None, None, None): (
        "duplicate_settlement_row",
        "The settlement report claims more for this payout than the bank actually credited, "
        "and the fee arithmetic on the rest of the group is sound. The usual cause is one "
        "transaction emitted twice in the report: the payout was only ever funded once.",
        "0.55",
    ),
    ("bank_credit_without_settlement_group", None, "over", None, None, None, None, None): (
        "bank_credit_unmatched",
        "Money arrived in the bank with no settlement report referencing that UTR. Until a "
        "platform statement turns up for it, nobody can say which orders it pays for.",
        "0.90",
    ),
    ("not_funded_by_bank_credit", "flipkart", "over", None, None, None, None, None): (
        "duplicate_settlement_row",
        "This row sits inside a payout the bank did not fund to that level. An identical "
        "transaction almost certainly appears elsewhere in the same report; the platform "
        "paid it once and reported it twice.",
        "0.70",
    ),
    ("not_funded_by_bank_credit", "myntra", "over", None, None, None, None, None): (
        "duplicate_settlement_row",
        "The payout is short by exactly this row's value. Re-emission of an already-settled "
        "transaction is the ordinary explanation; a genuinely unpaid row would normally "
        "appear as a shortfall across the whole group rather than one clean surplus line.",
        "0.70",
    ),
    ("not_funded_by_bank_credit", "amazon", "over", None, None, None, None, None): (
        "duplicate_settlement_row",
        "The credit does not cover this row and covers everything else in the group exactly. "
        "That is the signature of a duplicated report line, not of money withheld.",
        "0.70",
    ),
    # -- commission ---------------------------------------------------------- #
    ("fee_variance_outside_tolerance", "myntra", "short", "8.8", "-3.7", None, None, None): (
        "commission_rate_stale",
        "Myntra is charging 8.8% more commission than the books expect, and it is the same "
        "8.8% on every order rather than drifting. A fixed proportional gap like that is a "
        "rate that changed on the platform's side and was never updated in our master file.",
        "0.85",
    ),
    ("fee_variance_outside_tolerance", "amazon", "short", "7.5", "-2.4", None, None, None): (
        "commission_rate_stale",
        "Amazon's commission is coming in 7.5% above the ledger's expectation, identically on "
        "every affected order. A constant percentage error is a stale master rate, not "
        "order-level noise.",
        "0.85",
    ),
    ("fee_variance_outside_tolerance", "flipkart", "short", "41.2", "-10.5", None, None, None): (
        "commission_slab_change",
        "Flipkart's fee is far above expectation on this order but the gap is not the same "
        "size as on other Flipkart orders. That points to the item being repriced into a "
        "different commission slab rather than to the whole channel's rate moving.",
        "0.60",
    ),
    ("fee_variance_outside_tolerance", "flipkart", "short", "26.3", "-7.7", None, None, None): (
        "commission_slab_change",
        "A large one-off commission overcharge on Flipkart with no matching gap on the "
        "channel's other orders. Most consistent with this category having been moved to a "
        "higher slab.",
        "0.60",
    ),
    ("fee_variance_outside_tolerance", "flipkart", "short", "22.0", "-7.8", None, None, None): (
        "commission_slab_change",
        "Commission overcharged well beyond tolerance on a single Flipkart order while the "
        "channel's other orders bill correctly. A slab reclassification on this category "
        "explains it; a channel-wide rate change would not.",
        "0.60",
    ),
    # -- other fee components ------------------------------------------------ #
    ("fee_variance_outside_tolerance", "website", "short", "74.8", "-1.8", None, None, None): (
        "fee_mismatch_other",
        "The website gateway fee is well above the flat 2% the books assume, but the net is "
        "only 1.8% short, so the absolute money is small. This looks like a payment-processing "
        "or instrument-specific charge the ledger formula does not model.",
        "0.75",
    ),
    ("fee_variance_outside_tolerance", "website", "short", "91.1", "-2.2", None, None, None): (
        "fee_mismatch_other",
        "Gateway charges close to double the modelled 2%, with a small rupee impact. The fee "
        "formula in the books is incomplete rather than the rate being wrong.",
        "0.75",
    ),
    ("fee_variance_outside_tolerance", "offline", "short", "129.9", "-3.1", None, None, None): (
        "fee_mismatch_other",
        "The POS acquirer took more than twice the 2% the ledger models. Card-present pricing "
        "varies by instrument, so this is a fee the books do not break out rather than a "
        "commission problem.",
        "0.70",
    ),
    # -- promo co-funding shapes --------------------------------------------- #
    ("fee_variance_outside_tolerance", "myntra", "short", "40.0", "-10.6", None, None, None): (
        "promo_cofunding_deduction",
        "Deductions far above the commission the books expect, varying order to order rather "
        "than sitting at a fixed percentage. That pattern usually means a campaign cost was "
        "shared back to the seller. A slab change is the other candidate and cannot be ruled "
        "out from the settlement report alone.",
        "0.45",
    ),
    ("fee_variance_outside_tolerance", "myntra", "short", "32.0", "-11.5", None, None, None): (
        "promo_cofunding_deduction",
        "A large, non-constant overcharge on Myntra with a double-digit hit to net. Most "
        "consistent with promotional co-funding billed against the order; a slab change would "
        "normally be uniform within a category.",
        "0.45",
    ),
    ("fee_variance_outside_tolerance", "myntra", "short", "28.6", "-12.2", None, None, None): (
        "promo_cofunding_deduction",
        "Commission billed far over expectation with net down 12%, and the size of the gap is "
        "not repeated across the channel. Campaign cost-sharing fits better than a rate or "
        "slab change, but the report does not itemise it.",
        "0.45",
    ),
    ("fee_variance_outside_tolerance", "amazon", "short", "30.0", "-8.0", None, None, None): (
        "promo_cofunding_deduction",
        "Amazon has deducted well beyond its commission on this order, and by a different "
        "amount than on other affected orders. A shared promotional cost is the usual "
        "explanation for a variable deduction of this size.",
        "0.45",
    ),
    ("fee_variance_outside_tolerance", "amazon", "short", "25.0", "-8.5", None, None, None): (
        "promo_cofunding_deduction",
        "Deduction materially above commission on a single Amazon order, not matching the "
        "channel's usual gap. Campaign co-funding is the most likely, though the report gives "
        "no line item to confirm it.",
        "0.45",
    ),
    ("fee_variance_outside_tolerance", "amazon", "short", "27.3", "-8.2", None, None, None): (
        "promo_cofunding_deduction",
        "An outsized, one-off commission overcharge on Amazon with net down 8%. Consistent "
        "with a promotional cost billed back to the seller rather than with a rate change.",
        "0.45",
    ),
    ("fee_variance_outside_tolerance", "amazon", "short", "33.3", "-7.7", None, None, None): (
        "promo_cofunding_deduction",
        "The fee is a third above what the books expect on this order alone. Where the gap "
        "does not repeat at a constant rate, a co-funded campaign deduction is the more "
        "likely reading.",
        "0.45",
    ),
    # -- holds and short payments -------------------------------------------- #
    ("payment_withheld_on_hold", "flipkart", "short", "0.0", "-100.0", None, None, None): (
        "weight_dispute_hold",
        "Flipkart reported the sale, took its commission at exactly the expected rate, and "
        "paid out nothing. A full hold with a correct fee is a dispute in progress — on "
        "Flipkart, almost always a shipping weight discrepancy. Held is not lost.",
        "0.85",
    ),
    ("net_variance_outside_tolerance", "flipkart", "short", "0.0", "-12.0", None, None, None): (
        "short_payment_unexplained",
        "Commission is exactly right and the net is still 12% short. Nothing in the "
        "settlement report accounts for the gap, so it has to be raised with the platform "
        "rather than corrected in our books.",
        "0.70",
    ),
    ("net_variance_outside_tolerance", "myntra", "short", "0.0", "-10.0", None, None, None): (
        "short_payment_unexplained",
        "The fee matches the books and the payout is still 10% light, with no deduction line "
        "explaining it. That is money the platform owes until it produces a reason.",
        "0.70",
    ),
    ("net_variance_outside_tolerance", "website", "short", "0.0", "-100.0", None, None, None): (
        "chargeback_deduction",
        "The entire payment was pulled back on a website order while the gateway fee stayed "
        "as booked. On card-acquired sales a full clawback with the fee retained is a "
        "cardholder dispute, not a refund we issued.",
        "0.70",
    ),
    # -- overpayments and rounding ------------------------------------------- #
    ("net_variance_outside_tolerance", "amazon", "over", "0.0", "0.1", None, None, None): (
        "rounding_variance",
        "Net is over by a tenth of a percent with the commission exactly as expected. That is "
        "paise-level drift from how the platform rounds its tax components, not a real "
        "difference.",
        "0.70",
    ),
    ("net_variance_outside_tolerance", "myntra", "over", "0.0", "1.4", None, None, None): (
        "fee_mismatch_other",
        "Slightly more was paid than the books expected, with commission exactly right, so a "
        "smaller-than-modelled ancillary charge is the likely explanation. Too large to be "
        "rounding and too small to be a rate change; worth a look rather than a conclusion.",
        "0.45",
    ),
    ("net_variance_outside_tolerance", "flipkart", "over", "0.0", "1.3", None, None, None): (
        "fee_mismatch_other",
        "A small overpayment with correct commission points at a fulfilment or shipping "
        "charge the ledger models higher than the platform actually billed.",
        "0.45",
    ),
    # -- settlement timing --------------------------------------------------- #
    ("settlement_outside_date_window", "amazon", "flat", "0.0", "0.0", None, "8-14", None): (
        "settlement_lag_crossing_batch",
        "The money is exactly right and it simply arrived one to two weeks past the normal "
        "cycle, so the sale and its payout fall in different reporting periods. Late is not "
        "missing.",
        "0.90",
    ),
    ("settlement_outside_date_window", "amazon", "flat", "0.0", "0.0", None, "1-7", None): (
        "settlement_lag_crossing_batch",
        "A correct payout that slipped a few days past the 21-day window and landed in the "
        "next batch. Nothing is short; only the cycle moved.",
        "0.88",
    ),
    ("settlement_overdue_beyond_window", "amazon", "flat", None, None, None, None, None): (
        "missing_settlement_row",
        "The order is on our books and the settlement window has closed with no line for it "
        "anywhere in the platform's report. Until Amazon produces one, this is unpaid rather "
        "than late.",
        "0.75",
    ),
    ("settlement_overdue_beyond_window", "myntra", "flat", None, None, None, None, None): (
        "missing_settlement_row",
        "Booked on our side, past the settlement window, and absent from Myntra's report "
        "entirely. That is a payment to chase, not a variance to explain.",
        "0.75",
    ),
    # -- books ahead of the platform ----------------------------------------- #
    ("paid_against_fully_reversed_order", "amazon", "over", None, None, None, None, None): (
        "refund_timing_lag",
        "Our books have already written this order down to zero because the refund was issued, "
        "and Amazon has paid it out in full because it has not deducted the return yet. The "
        "deduction should appear in a later settlement.",
        "0.80",
    ),
    ("fee_variance_outside_tolerance", "amazon", "over", "-100.0", "0.0", None, "8-14", None): (
        "refund_timing_lag",
        "The payment and its reversal have now both landed, so the net is right and no "
        "commission is left on the order. What remains is that the two halves fell in "
        "different cycles.",
        "0.50",
    ),
    ("fee_variance_outside_tolerance", "amazon", "over", "-100.0", "0.0", None, "1-7", None): (
        "refund_timing_lag",
        "Commission has washed out to nothing and the net ties, which happens once a refund "
        "catches up with the sale it belongs to. The timing gap is the only finding.",
        "0.50",
    ),
    ("fee_variance_outside_tolerance", "myntra", "over", "-100.0", "0.0", None, "8-14", None): (
        "refund_timing_lag",
        "No commission retained and the net matches, so the reversal has caught up with the "
        "original payment a cycle later than the books recorded it.",
        "0.50",
    ),
    # -- late deductions ------------------------------------------------------ #
    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "15-21", None, "refund"): (
        "rto_reversal_later_cycle",
        "Flipkart is taking the money back two to three weeks after this order settled and "
        "closed at full value. Our books never recorded a refund, so this is a returned "
        "shipment working its way back, not a refund we issued.",
        "0.72",
    ),
    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "8-14", None, "refund"): (
        "rto_reversal_later_cycle",
        "A deduction against an order the books already closed at full value on Flipkart. "
        "With no refund booked on our side, a return-to-origin is the likely reason; a lagged "
        "refund would have shown the order written down first.",
        "0.65",
    ),
    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "1-7", None, "refund"): (
        "rto_reversal_later_cycle",
        "Flipkart deducted against an order that settled clean the week before. The books show "
        "no refund for it, which points at a return rather than at a refund landing late.",
        "0.62",
    ),
    ("late_row_for_already_settled_order", "amazon", "short", None, None, "8-14", None, "refund"): (
        "refund_timing_lag",
        "Amazon is deducting a refund one to two weeks after the order settled. Refunds we "
        "issue are booked immediately and deducted on the platform's next cycle, so this is "
        "the deduction catching up rather than a return.",
        "0.62",
    ),
    ("late_row_for_already_settled_order", "amazon", "short", None, None, "1-7", None, "refund"): (
        "refund_timing_lag",
        "A refund deduction landing in the cycle after the sale settled. The money is expected; "
        "only the period it fell in differs from the books.",
        "0.60",
    ),
    # -- late adjustments ----------------------------------------------------- #
    ("late_row_for_already_settled_order", "myntra", "short", None, None, "1-7", None, "adjustment"): (
        "tcs_timing_mismatch",
        "An adjustment pulling money back shortly after settlement, on a marketplace channel. "
        "That is the shape of TCS collected in a later cycle than the sale it belongs to.",
        "0.55",
    ),
    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "1-7", None, "adjustment"): (
        "tcs_timing_mismatch",
        "A small marketplace adjustment taken after the order closed, consistent with tax "
        "collected at source being recovered a cycle late. Needs a tax review rather than a "
        "reconciliation fix.",
        "0.50",
    ),
    ("late_row_for_already_settled_order", "amazon", "short", None, None, "1-7", None, "adjustment"): (
        "tds_timing_mismatch",
        "An adjustment recovering a withholding amount after the order had settled. On Amazon "
        "this is normally 194-O deduction landing in a later cycle than the payment.",
        "0.50",
    ),
    ("late_row_for_already_settled_order", "flipkart", "over", None, None, "8-14", None, "adjustment"): (
        "short_payment_unexplained",
        "Money coming back in against an order that had already closed. It reads like a "
        "reimbursement for an earlier underpayment; without the platform's reference for it, "
        "the original shortfall is still the finding.",
        "0.40",
    ),
    ("late_row_for_already_settled_order", "myntra", "over", None, None, "22+", None, "adjustment"): (
        "missing_settlement_row",
        "A credit arriving more than three weeks after the order closed, with no settlement "
        "line of its own. Most likely the platform paying for an order it never reported at "
        "the time.",
        "0.45",
    ),
}


# --------------------------------------------------------------------------- #
# Checkpoint 4 -- claim narratives
# --------------------------------------------------------------------------- #

# (platform, cause) -> (subject, statement, request). One answer per *kind* of claim,
# because the prompt carries no order and no amount -- see pipeline/llm/drafts.py.
# Not one numeral anywhere below: the schema rejects them, and every figure in the
# finished letter is substituted from the matcher's verdicts.
NARRATIVES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("amazon", "missing_settlement_row"): (
        "Missing settlement line for a delivered order",
        "Our books record this order as sold, dispatched and delivered, and the payout "
        "window for it has now closed. The settlement reports issued to us contain no line "
        "for the order at all, so no commission, no tax collection and no net payout have "
        "been reported against it.",
        "Please reissue the settlement line and release the net due, or tell us the "
        "settlement reference under which it was already remitted.",
    ),
    ("myntra", "missing_settlement_row"): (
        "Order sold and delivered with no settlement line issued",
        "This order appears in our books as sold and delivered and its expected payout date "
        "has passed. No settlement line for it exists in any report sent to us, so nothing "
        "has been reported against the order and nothing has been paid.",
        "Please issue the settlement line for this order and pay the net due, or confirm "
        "the settlement reference it was remitted under.",
    ),
    ("flipkart", "short_payment_unexplained"): (
        "Payout below the net due, with nothing on the report to explain it",
        "The amount credited against this order is below the net our books expect once the "
        "agreed commission and statutory collections are applied. Nothing in the settlement "
        "report — no fee line, no adjustment and no deduction — accounts for the difference.",
        "Please itemise the deduction that produced this shortfall or remit the balance.",
    ),
    ("myntra", "short_payment_unexplained"): (
        "Unexplained shortfall against the net due on this order",
        "The credit received for this order falls short of the net our books expect after "
        "the agreed commission and statutory collections. The settlement report carries no "
        "deduction, adjustment or fee line that explains the gap.",
        "Please identify the deduction responsible or release the outstanding balance.",
    ),
    ("flipkart", "weight_dispute_hold"): (
        "Payout held against an open shipping-weight discrepancy",
        "This order is reported as sold and your commission has been retained against it, "
        "but no payout has been released because a shipping-weight discrepancy is open. Our "
        "dispatch record for the consignment matches the weight declared at manifest.",
        "Please review the weight evidence and release the held payout, or share the "
        "measurement record you are relying on.",
    ),
    ("amazon", "promo_cofunding_deduction"): (
        "Promotional co-funding deducted without prior notice",
        "A deduction has been taken against this order as a seller share of a promotional "
        "campaign cost, over and above the agreed referral commission. We hold no record of "
        "accepting that campaign, and no itemised breakdown accompanied the charge.",
        "Please itemise the campaign this charge relates to and reverse it where our "
        "participation was not confirmed in writing.",
    ),
    ("myntra", "promo_cofunding_deduction"): (
        "Campaign cost charged back without an agreed enrolment",
        "The deduction taken against this order exceeds what our rate card provides for, and "
        "the excess is described as a share of promotional cost. No enrolment in that "
        "campaign was confirmed with us and no breakdown accompanied the charge.",
        "Please provide the itemised campaign charge and reverse it where enrolment cannot "
        "be evidenced.",
    ),
    ("website", "chargeback_deduction"): (
        "Chargeback debited without a representment window",
        "A chargeback has been debited against this settled order. We were not notified of "
        "the dispute before the debit was taken and were given no opportunity to submit "
        "delivery evidence in response.",
        "Please reopen the dispute for representment and hold the debit until our evidence "
        "has been assessed.",
    ),
}


# --------------------------------------------------------------------------- #
# Checkpoint 4 -- intent mapping
# --------------------------------------------------------------------------- #

# question -> the mapping. Three of the eleven are not answers, and they are the
# interesting three: one clarification and two refusals. See tools/operator_questions.py.
INTENTS: dict[str, dict[str, Any]] = {
    "How much did we actually get paid by each channel?": {
        "outcome": "mapped",
        "metric_id": "net_revenue_by_channel",
        "group_by": "channel",
        "restatement": "Net revenue settled — money that actually reached the bank after "
                       "every platform deduction — totalled per channel across all ten weeks.",
    },
    "Is Myntra taking a bigger cut than it used to?": {
        "outcome": "mapped",
        "metric_id": "effective_take_rate",
        "group_by": "batch",
        "channel": "myntra",
        "restatement": "Myntra's effective take rate — commission, GST on commission, TCS "
                       "and TDS as a percentage of gross order value — plotted week by week.",
    },
    "What share of gross are the platforms keeping across the board?": {
        "outcome": "mapped",
        "metric_id": "effective_take_rate",
        "group_by": "channel",
        "restatement": "The effective take rate — every deduction as a percentage of gross "
                       "order value — for each channel across the whole corpus.",
    },
    "Which causes are generating the most exceptions?": {
        "outcome": "mapped",
        "metric_id": "exception_count_by_cause",
        "group_by": "cause",
        "restatement": "A count of exceptions by cause across all ten weeks, largest first.",
    },
    "Is the manual review rate actually coming down?": {
        "outcome": "mapped",
        "metric_id": "review_rate_trend",
        "group_by": "batch",
        "restatement": "The manual review rate — settlement rows still needing a human after "
                       "learned rules fire, as a percentage of the batch — plotted per week.",
    },
    "How much money are we still chasing, by platform?": {
        "outcome": "mapped",
        "metric_id": "open_claim_value",
        "group_by": "platform",
        "restatement": "The rupee value of claims still open, totalled per platform.",
    },
    "How much have we lost to claims that expired before we filed them?": {
        "outcome": "mapped",
        "metric_id": "rupees_expired_unrecovered",
        "group_by": "batch",
        "restatement": "Rupees on claims whose filing window closed with no recovery, shown "
                       "for the week each one lapsed in.",
    },
    "Show me net revenue by channel for the first four weeks only": {
        "outcome": "mapped",
        "metric_id": "net_revenue_by_channel",
        "group_by": "channel",
        "from_batch": 1,
        "to_batch": 4,
        "restatement": "Net revenue settled per channel, restricted to batches one through "
                       "four.",
    },
    "How are our fees trending?": {
        "outcome": "clarify",
        "restatement": "Two different metrics answer this and they diverge by several "
                       "percentage points, so nothing has been computed yet.",
        "clarifying_question": "Do you mean the platform commission on its own, or every "
                               "deduction including the GST charged on that commission and "
                               "the tax collected at source?",
    },
    "Which of our SKUs are least profitable?": {
        "outcome": "refuse",
        "restatement": "Nothing has been computed: the registry has no metric that answers "
                       "this question.",
        "refusal": "This reconciliation holds orders, settlements and bank credits. It has "
                   "no product master and no cost of goods, so profitability per SKU cannot "
                   "be computed here at all — not approximately, and not from an adjacent "
                   "figure.",
    },
    "What will next month's settlement come to?": {
        "outcome": "refuse",
        "restatement": "Nothing has been computed: the registry measures what happened and "
                       "does not forecast.",
        "refusal": "Every metric in the registry is a measurement over settled batches. "
                   "There is no forecasting metric, and projecting one of these series "
                   "forward would produce a number with a reconciliation's authority and a "
                   "guess's accuracy.",
    },
}

# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _signature(question: Question) -> tuple:
    return tuple(question.to_json().values())


def _entry(
    ask: Ask, task: str, payload: dict[str, Any], model: str, chars_per_token: Decimal
) -> CacheEntry:
    """Build a cache entry for one authored answer.

    The key comes from the same :func:`key_for` the live client uses, over the same
    rendered prompt and the same JSON schema, so swapping the transport back to the
    HTTP API hits these entries rather than re-asking.
    """
    schema = json_schema(ask.output)
    return CacheEntry(
        key=key_for(model=model, system=ask.system, user=ask.user, schema=schema),
        task=task,
        model=model,
        source=SOURCE_TRANSCRIPT,
        request={"system": ask.system, "user": ask.user, "tool": ask.tool_name},
        response=payload,
        input_tokens=estimate_tokens(ask.system + ask.user, chars_per_token),
        output_tokens=estimate_tokens(json.dumps(payload, ensure_ascii=False), chars_per_token),
    )


def write_hypotheses(cache: ResponseCache, model: str, chars: Decimal) -> int:
    from pipeline.cases import FindingLog as _Log

    log = _Log()
    cases = [
        case
        for result in run_all()
        for case in build_cases(result, log)
        if case.features.bucket != Bucket.QUARANTINED.value
    ]
    written = 0
    for question in questions_in(cases):
        signature = _signature(question)
        if signature not in HYPOTHESES:
            raise KeyError(f"no authored hypothesis for {signature}")
        cause, text, confidence = HYPOTHESES[signature]
        payload = {"cause": cause, "hypothesis": text, "confidence": confidence}
        Hypothesis.model_validate(payload)
        cache.put(_entry(hypothesis_ask(question), "hypothesis", payload, model, chars))
        written += 1
    return written


def write_inductions(cache: ResponseCache, model: str, chars: Decimal) -> int:
    from pipeline.cases import FindingLog as _Log
    from pipeline.llm.hypotheses import question_for
    from pipeline.rules.resolutions import load as load_operator_log
    from tools.induced_rules import OVERRIDE_RULES, resolve

    log = _Log()
    cases = {case.case_id: case for result in run_all() for case in build_cases(result, log)}
    written = 0
    for resolution in load_operator_log().resolutions:
        case = cases[resolution.case_id]
        signature = _signature(question_for(case.features))
        target = OVERRIDE_RULES.get((resolution.batch, signature), signature)
        payload = resolve(target)
        if payload is None:
            raise KeyError(f"no authored rule for {signature}")
        InducedRule.model_validate(payload)
        cache.put(
            _entry(induction_ask(resolution.text, case.features), "induction", payload, model, chars)
        )
        written += 1
    return written


#: A narrative that satisfies the schema and is never written to disk. It exists only
#: so that a shape-collecting run can complete; the real answers are in NARRATIVES.
PROBE = ClaimNarrative(
    subject="Placeholder subject for a shape-collecting run",
    statement="This narrative exists only to let a discovery run finish and is never cached.",
    request="Discard this text; it is not a claim.",
)


def claim_draft_shapes() -> set[tuple[str, str]]:
    """Every (platform, cause) a real run actually asks for a claim draft on.

    Derived rather than declared. The alternative -- a hand-written list -- goes stale
    the first time the corpus grows a shape, and the failure mode is a claim reaching
    an operator with no words around it.
    """
    from pipeline.learn import run as run_learning

    shapes: set[tuple[str, str]] = set()

    def collect(platform: str, cause: str, batch: int) -> ClaimNarrative:
        shapes.add((platform, cause))
        return PROBE

    run_learning(narrator=collect)
    return shapes


def write_narratives(cache: ResponseCache, model: str, chars: Decimal) -> int:
    """One narrative per (platform, cause) the register actually opens a draft for.

    The pairs are read off a real run rather than listed by hand, so a shape that
    starts appearing in the corpus fails loudly here instead of arriving in front of
    an operator with no words around it.
    """
    written = 0
    for platform, cause in sorted(claim_draft_shapes()):
        answer = NARRATIVES.get((platform, cause))
        if answer is None:
            raise KeyError(f"no authored claim narrative for {(platform, cause)}")
        subject, statement, request = answer
        payload = {"subject": subject, "statement": statement, "request": request}
        ClaimNarrative.model_validate(payload)
        cache.put(
            _entry(draft_ask(DraftQuestion(platform, cause)), "claim_draft", payload, model, chars)
        )
        written += 1
    return written


def write_intents(cache: ResponseCache, model: str, chars: Decimal) -> int:
    """One answer per question the operator asked. Billed to batch 10, where they asked."""
    from tools.operator_questions import QUESTIONS

    written = 0
    for asked in QUESTIONS:
        payload = INTENTS.get(asked.question)
        if payload is None:
            raise KeyError(f"no authored intent mapping for {asked.question!r}")
        MetricIntent.model_validate(payload)
        cache.put(_entry(intent_ask(asked.question), "intent", payload, model, chars))
        written += 1
    return written


def main() -> int:
    pricing = load_yaml(CONFIG_DIR / "pricing.yaml")
    model = str(pricing["model"])
    chars = Decimal(str(pricing["estimated_chars_per_token"]))
    cache = ResponseCache()

    hypotheses = write_hypotheses(cache, model, chars)
    inductions = write_inductions(cache, model, chars)
    narratives = write_narratives(cache, model, chars)
    intents = write_intents(cache, model, chars)
    entries = cache.entries()
    print(
        f"{hypotheses} hypothesis questions, {inductions} induction prompts, "
        f"{narratives} claim narratives, {intents} intent mappings"
    )
    print(f"{len(entries)} distinct cache entries -> {cache.directory}")
    print(f"  {sum(e.input_tokens for e in entries)} input + "
          f"{sum(e.output_tokens for e in entries)} output tokens (estimated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
