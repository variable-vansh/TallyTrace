"""The bookkeeper's own words, one note per kind of exception.

For this build I am the human. These are the notes I would type while clearing the
queue: prose, with the reasoning a person actually has, referring to slabs and
account managers and what the books already show. None of them is written in the
shape the rule engine wants. Two of them are deliberately loose, because that is how
people write, and one of those loose notes induces a rule that over-matches and
retires itself three batches later. Cleaning that up would have removed the only
real evidence that the lifecycle works.

Notes are keyed by the *question* a case asks -- the same normalised signature the
hypothesis prompt uses -- because a person clearing the twentieth identical Myntra
variance types the same sentence they typed for the first. Where the operator's
understanding genuinely changed between batches, ``OVERRIDES`` records the batch it
changed in.
"""

from __future__ import annotations

OPERATOR = "priya.n@demostore.in"

# question signature -> what the operator typed
NOTES: dict[tuple, str] = {
    ("fee_variance_outside_tolerance", "myntra", "short", "8.8", "-3.7", None, None, None):
        "Myntra is billing 27.2% on these but our master rate sheet still says 25%. Their "
        "category manager flagged in the January mailer that outerwear moved up a slab. Our "
        "rate file is out of date, not their invoice. Update the Myntra rate to 27.2% and "
        "these all close.",

    ("fee_variance_outside_tolerance", "amazon", "short", "7.5", "-2.4", None, None, None):
        "Same story as Myntra. Amazon has been charging 21.5% since the referral fee revision "
        "and our sheet is stuck at 20%. Nothing wrong with their maths, ours is stale. Fix the "
        "Amazon rate at 21.5%.",

    ("paid_against_fully_reversed_order", "amazon", "over", None, None, None, None, None):
        "We already refunded this customer and wrote the order down to zero, but Amazon has "
        "paid us the full amount because their return deduction hasn't run yet. It'll come off "
        "the next settlement. Nothing to chase, just a timing gap between our books and theirs.",

    ("payment_withheld_on_hold", "flipkart", "short", "0.0", "-100.0", None, None, None):
        "Flipkart has taken their commission and held the payout on this one. It's their weight "
        "dispute process - they measured the parcel heavier than we declared. The money is held, "
        "not gone, and it releases when the dispute closes. Raise it with them, don't write it off.",

    ("bank_group_sum_mismatch", None, "short", None, None, None, None, None):
        "The payout report adds up to more than what actually hit the bank, and the row it "
        "singles out is the same transaction as one already in the group. Their report has "
        "emitted the line twice. The bank is right.",

    ("not_funded_by_bank_credit", "flipkart", "over", None, None, None, None, None):
        "Duplicate line in the settlement file again. We were paid once and reported twice, so "
        "the row that doesn't fit the credit is the copy.",

    ("not_funded_by_bank_credit", "myntra", "over", None, None, None, None, None):
        "Another repeated line in the report - the credit covers everything except this one row "
        "and there's an identical row above it. Ignore the copy.",

    ("not_funded_by_bank_credit", "amazon", "over", None, None, None, None, None):
        "Same duplicated row problem. The payout funded the group once.",

    ("bank_credit_without_settlement_group", None, "over", None, None, None, None, None):
        "Money in the account and nothing in any settlement report that references this UTR. I "
        "can't allocate it until someone tells me what it is. Leave it open and ask the bank for "
        "the remitter details.",

    # -- the loose one. See OVERRIDES: this is what I wrote in batch 2, before I
    #    understood that Amazon's late deductions are a different animal.
    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "1-7", None, "refund"):
        "A deduction landing a week after we'd already been paid and closed the order. That's a "
        "return coming back through - happens on all the marketplaces, the goods take time to "
        "get to the warehouse. Net it off against the original sale.",

    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "8-14", None, "refund"):
        "Flipkart clawing back the full order value a fortnight after settling it, and there's no "
        "refund on our side for this order. That's an RTO - the parcel came back. Their returns "
        "run two to three weeks behind the sale.",

    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "15-21", None, "refund"):
        "Another return coming back on Flipkart, three weeks after the sale settled. We never "
        "booked a refund for it so it isn't a refund catching up - the goods physically came back. "
        "Expect these to keep landing two to three weeks late.",

    ("late_row_for_already_settled_order", "amazon", "short", None, None, "8-14", None, "refund"):
        "This is not a return. We issued this refund ourselves and booked it at the time; Amazon "
        "just deducts it on whichever settlement runs next, usually a week or two later. Different "
        "thing from the Flipkart claw-backs even though it looks the same on the report.",

    ("late_row_for_already_settled_order", "amazon", "short", None, None, "1-7", None, "refund"):
        "Refund we'd already booked, deducted by Amazon on the following cycle. Timing only.",

    ("late_row_for_already_settled_order", "myntra", "short", None, None, "1-7", None, "adjustment"):
        "This is the TCS they didn't collect at the time of the sale, recovered on a later "
        "settlement. Tax timing, and I'd rather our accountant looked at it than have it netted "
        "off automatically.",

    ("late_row_for_already_settled_order", "flipkart", "short", None, None, "1-7", None, "adjustment"):
        "Same as the Myntra one - TCS being picked up a cycle late. Send it to the tax review pile.",

    ("late_row_for_already_settled_order", "amazon", "short", None, None, "1-7", None, "adjustment"):
        "This is the 194-O withholding coming off later than the payment it belongs to. Tax, not "
        "reconciliation. Our accountant should see it.",

    ("late_row_for_already_settled_order", "flipkart", "over", None, None, "8-14", None, "adjustment"):
        "Money coming back to us against an order that closed weeks ago, with no reference on it. "
        "I think it's them settling the shortfall I raised, but I'm not closing it until they "
        "confirm which claim it's against.",

    ("late_row_for_already_settled_order", "myntra", "over", None, None, "22+", None, "adjustment"):
        "A credit turning up a month later for an order that never appeared in their report at "
        "the time. Looks like they've finally paid for it. Keep the claim open until it's matched.",

    ("settlement_outside_date_window", "amazon", "flat", "0.0", "0.0", None, "8-14", None):
        "Nothing wrong with the money on this one, it's correct to the paise. The payout just "
        "came a week or two after their normal cycle so the sale and the payment fell in "
        "different weeks. Not worth anyone's time.",

    ("settlement_outside_date_window", "amazon", "flat", "0.0", "0.0", None, "1-7", None):
        "Late payout, right amount. Their cycle slipped a few days. Nothing to do.",

    ("settlement_overdue_beyond_window", "amazon", "flat", None, None, None, None, None):
        "The window has closed and there is still no line for this order anywhere in Amazon's "
        "report. That's unpaid, not late. Needs a SAFE-T claim before the 30 days run out.",

    ("settlement_overdue_beyond_window", "myntra", "flat", None, None, None, None, None):
        "Booked on our side, past the settlement window, nothing from Myntra. Chase it as a "
        "missing payment.",

    ("net_variance_outside_tolerance", "flipkart", "short", "0.0", "-12.0", None, None, None):
        "Commission is exactly right and they're still 12% light on the payout with no deduction "
        "line to explain it. I've no idea what this is and neither does the report. Raising it "
        "with the account manager.",

    ("net_variance_outside_tolerance", "myntra", "short", "0.0", "-10.0", None, None, None):
        "Fee is correct, payout is 10% short, nothing itemised. Same as the Flipkart ones - it "
        "goes to them, not into our books.",

    ("net_variance_outside_tolerance", "website", "short", "0.0", "-100.0", None, None, None):
        "The whole payment was pulled back on the website order but the gateway kept its fee. "
        "That's a card dispute, not a refund we issued. Razorpay will have the chargeback "
        "reference. Do not net this off - we need to contest it.",

    ("net_variance_outside_tolerance", "amazon", "over", "0.0", "0.1", None, None, None):
        "A few paise over. That's just how their tax rounding falls. Ignore.",

    ("net_variance_outside_tolerance", "myntra", "over", "0.0", "1.4", None, None, None):
        "Slightly more than expected with the commission exactly right, so some shipping charge "
        "we budget for wasn't taken. Small enough to write off.",

    ("net_variance_outside_tolerance", "flipkart", "over", "0.0", "1.3", None, None, None):
        "Paid a bit over because a fulfilment charge we model didn't get billed. Write it off.",

    ("fee_variance_outside_tolerance", "website", "short", "74.8", "-1.8", None, None, None):
        "Our website fee formula assumes a flat 2% and the gateway actually charges more on some "
        "instruments - netbanking and the wallets cost more than cards. The rupees are small. "
        "Write these off rather than chase them.",

    ("fee_variance_outside_tolerance", "website", "short", "91.1", "-2.2", None, None, None):
        "Gateway charging above our 2% assumption again, same instrument-mix reason. Small money, "
        "write it off.",

    ("fee_variance_outside_tolerance", "offline", "short", "129.9", "-3.1", None, None, None):
        "Card-present pricing at the counter isn't a flat 2% either. Our POS fee model is too "
        "simple. Small amount, write it off.",

    ("fee_variance_outside_tolerance", "flipkart", "short", "41.2", "-10.5", None, None, None):
        "Flipkart has put this item in a different commission slab from the one we priced it at. "
        "It's not the whole channel - their other orders this week bill correctly - so it's a "
        "category reclassification on this line.",

    ("fee_variance_outside_tolerance", "flipkart", "short", "26.3", "-7.7", None, None, None):
        "Another one Flipkart has moved into a higher slab. Their rate card changed for this "
        "category and we priced it on the old one.",

    ("fee_variance_outside_tolerance", "flipkart", "short", "22.0", "-7.8", None, None, None):
        "Same slab reclassification on Flipkart. One category, not the whole channel.",

    ("fee_variance_outside_tolerance", "amazon", "over", "-100.0", "0.0", None, "8-14", None):
        "Payment and its reversal have both landed now, so it nets to zero and no commission is "
        "left on it. The only oddity is that the two halves fell in different weeks.",

    ("fee_variance_outside_tolerance", "amazon", "over", "-100.0", "0.0", None, "1-7", None):
        "Sale and refund both settled, nets out, no fee left. Timing only.",

    ("fee_variance_outside_tolerance", "myntra", "over", "-100.0", "0.0", None, "8-14", None):
        "Reversal caught up with the sale a cycle later. Nets to zero, nothing to do.",

    ("fee_variance_outside_tolerance", "myntra", "short", "40.0", "-10.6", None, None, None):
        "This is not the rate problem. The deduction is far bigger than the commission gap and "
        "it's a different size on every order. They've charged us a share of a campaign we never "
        "signed off on. I want this itemised before I accept it.",

    ("fee_variance_outside_tolerance", "myntra", "short", "32.0", "-11.5", None, None, None):
        "Another campaign co-funding deduction dressed up as commission. Different amount again. "
        "Don't close these - they owe us an explanation.",

    ("fee_variance_outside_tolerance", "myntra", "short", "28.6", "-12.2", None, None, None):
        "Same unexplained promotional charge on Myntra. Query it with them.",

    ("fee_variance_outside_tolerance", "amazon", "short", "30.0", "-8.0", None, None, None):
        "Amazon doing the same thing Myntra did - a promotional cost billed back to us without "
        "notice. Not the referral fee, that's separate and correct. Query it.",

    ("fee_variance_outside_tolerance", "amazon", "short", "25.0", "-8.5", None, None, None):
        "Another co-funded campaign deduction on Amazon that nobody agreed with us. Raise it.",

    ("fee_variance_outside_tolerance", "amazon", "short", "27.3", "-8.2", None, None, None):
        "Same promotional deduction pattern. It varies per order which is how I know it isn't the "
        "commission rate. Query.",

    ("fee_variance_outside_tolerance", "amazon", "short", "33.3", "-7.7", None, None, None):
        "Promotional cost shared back to us again. Not agreed. Query it.",
}

# (batch, question signature) -> what the operator typed *that* week, where their
# understanding had genuinely moved on. Only one entry, and it is the interesting one:
# the batch-2 note above generalised across every marketplace, and by batch 4 the
# operator had seen the system apply it to Amazon and knew it was wrong.
OVERRIDES: dict[tuple, str] = {
    (4, ("late_row_for_already_settled_order", "flipkart", "short", None, None, "1-7", None, "refund")):
        "Correcting what I wrote a couple of weeks ago: these claw-backs are a Flipkart thing "
        "specifically, they're returns physically coming back. Amazon's late deductions are "
        "refunds we already booked and they are not the same and shouldn't be treated the same.",
}
