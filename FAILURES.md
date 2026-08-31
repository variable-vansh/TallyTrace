# FAILURES

A running log of what went wrong and what it cost. Appended to as the build goes.
Entries are things that were actually wrong in this repo, not hypotheticals.

---

## Checkpoint 1 — Data Foundation

### 1. The bank statement was a 3:1 join, not an N:1 one

**What happened.** The first clean base grouped settlement rows into payouts by
`(channel, settled_at)`, taking `settled_at` straight from `created_at + lag`. Because
the lag was drawn per order, almost every order got its own payout date: 1362 settlement
rows produced 382 bank credits, about 3.5 rows per credit.

**Why it mattered.** Checkpoint 2 calls bank-level N:1 matching "the technically
substantial part of the whole build". At 3.5 rows per credit there is nothing
substantial to solve — a subset-sum over three candidates is not a subset-sum problem.
The generator would have quietly made the hardest part of the matcher trivial, and the
matcher would have passed its tests.

**Fix.** Added `payout_cadence` and `payout_weekday` to `config/channels.yaml`.
Marketplaces pay out on a fixed weekday, so a cycle's rows collapse onto one date and
one credit; gateways stay daily. Largest credit now aggregates 59 settlement rows.
`test_bank_credits_aggregate_many_settlement_rows` asserts it stays above 20.

### 2. Batch sizes were driven from the wrong end

**What happened.** Orders were generated per batch by creation date, and settlement rows
landed in whatever batch their payout fell into. With a 7-day batch and a 7-21 day
marketplace lag, rows drifted forward unpredictably: the row counts came out
`[73, 43, 64, 91, 83, 113, 128, 132, 149, 486]`. Batch 2 was under the 50-row floor and
batch 10 held every settlement that fell past the end of the corpus.

**Why it mattered.** The review rate is measured as a percentage of batch total. A batch
size that jumps around is a denominator that jumps around, and the whole learning curve
in checkpoint 3 would have been noise dressed as a trend.

**Fix.** Inverted the generation direction. `settlements_per_batch` now says how many
orders *settle* in each batch; the payout date is drawn inside the batch window and
`created_at` is derived backwards from the channel's lag. Batch size became exact by
construction, and the opening book — orders created before batch 1 — fell out for free
instead of needing a separate warm-up pass. The remaining drift comes only from
injectors moving rows between batches, which is small and was calibrated out: counts are
now `[59, 72, 85, 101, 114, 127, 141, 154, 167, 181]`.

### 3. The clean base did not reconcile, and the reason was correct behaviour

**What happened.** `verify_clean` reported three settlement groups with no bank credit.
All three were website payout days where the only order had been captured and refunded
the same day, netting to exactly zero.

**Why it mattered.** Nearly wrote a bug to fix it. A zero-value payout is never wired, so
the *absence* of a bank credit was right and the check was wrong.

**Fix.** `check_bank` now accepts a zero-sum group with no credit, and says why. Worth
recording because the instinct was to make the generator emit a ₹0.00 bank row, which
would have put a row in the data that no real bank statement contains.

### 4. Three defects the hand-inspection caught that the tests did not

Read fifty rows of batch 1 by hand, as the checkpoint asks. Three things were wrong that
every test was happy with:

- **`-0.00` in tax columns.** Negating a zero TCS line on a non-marketplace refund gave
  `Decimal('-0.00')`. Arithmetically fine, reads as a defect in a settlement file.
  `inr()` now normalises negative zero.
- **`CARD SALE TIDord_000028`.** The POS and Flipkart description templates prefix their
  own reference format, and the internal order id was being pasted in whole. Added a
  `{ref}` placeholder for the bare numeric part.
- **The `missing_order_id` malformed row was not malformed.** The model allowed a null
  `order_id` on any row, so the row meant to exercise the quarantine path validated
  cleanly and would have gone straight into the matcher with nothing to join on. The
  brief says `order_id` is "nullable for `adjustment` rows"; the model now enforces
  exactly that.

### 5. The near-miss was distinguishable, which made it worthless

**What happened.** `near_miss_fee_variance` repriced the settlement to 27.2% while
leaving the ledger on the order's own category rate. Myntra footwear carries 28%, so
those rows came out with a *negative* fee variance — the opposite sign from the
stale-rate rule they are supposed to be confused with. Caught by an assertion that every
recorded impact is positive.

**Why it mattered.** The near-miss is the single most valuable row in the dataset. A
near-miss that a rule can trivially reject tests nothing.

**Fix.** The injector now takes `ledger_rate` as well and reprices to the same pair the
stale-rate injector uses, so the two are numerically identical on the surface and differ
only in the answer key. `test_a_near_miss_is_numerically_indistinguishable_from_the_rule_it_trips`
asserts the two share a `(channel, rate)` signature.

### 6. RTO reversals could not appear in the first batches

**What happened.** An RTO reversal is recorded in the batch it *lands* in, so a batch-1
sale with a three-cycle offset first shows up in batch 4. Batches 1-3 had none of a cause
that is supposed to recur throughout.

**Fix.** Added short-offset entries off the opening book so the cause appears from batch
2. Batch 1 still has none and cannot: there is no earlier batch for its sale to have
happened in. Left as is rather than faked.

**Still open.** The same edge truncates the other cross-batch causes at the end of the
corpus — batch 10 has no sale-side lag, reversal or refund-lag entries, because there is
no batch 11 for them to land in. Batch 10 still *receives* those rows from batches 8 and
9. This is a real property of a closed ten-batch corpus, not a defect, but it is the
first thing to check if batch 10's numbers ever look unlike the rest.

---

## Checkpoint 2, part A — Deterministic Matcher

### 7. The first matcher silently cleared every late settlement

**What happened.** The matcher passed its own tests and put 56 injected
`settlement_lag_crossing_batch` rows in `matched`. The date-window rule had been read as
purely defensive — "a settlement arriving inside `date_window_days` is not missing money"
— so lateness only ever suppressed a flag and never raised one. A payout that arrived 31
days after the order, correct to the paise, reconciled clean.

**Why it mattered.** Two ways. The obvious one is the silent-clear count: 56 troubles the
harness would have scored as caught-by-accident. The worse one is that
`settlement_lag_crossing_batch` is one of the four *recurring* causes checkpoint 3 is
supposed to learn from. A cause the matcher never surfaces produces no exceptions, and a
learning loop with no exceptions to learn from would have looked like it was working
right up until the review-rate curve refused to move.

**Fix.** `settlement_delay_days` measures `settled_at - created_at` on the rows
themselves and fires `settlement_outside_date_window` past the window, as a `variance`
with an impact of ₹0.00 — late money is not missing money, and the rupee column should
not pretend otherwise. Money findings still outrank it, so a late row that is *also*
short reports the shortfall and records the delay in the detail. No threshold moved:
`date_window_days` is still 21, above every channel's own stated lag, so no clean row in
the corpus trips it.

### 8. The residual search named the wrong twin

**What happened.** `duplicate_settlement_row` injects an exact copy of a payment row that
the bank never funded. The N:1 search correctly found the payout over-reporting by one
row's worth and correctly named a row that explains it — the *original*, not the copy,
because the candidate pool broke ties on `entity_id` ascending. Scored against the answer
key, all three duplicates read as silent clears.

**Why it mattered.** Nearly "fixed" this by reversing the sort, which is answer-key
fitting with extra steps. The two rows are genuinely indistinguishable: same order, same
amount, same fee, same payout. Any rule that picks one is a convention, and the question
is whether the convention is defensible on its own terms.

**Fix.** Ties now break towards the row that appeared *later in the report* — first write
wins, the first occurrence is the transaction and the second is the re-emission. That is
a real reconciliation convention rather than a fitted one, and it is stated in the
docstring so the next reader can disagree with it on the merits.

### 9. "Fee variance of ₹813.24 against an expected fee of ₹0.00"

**What happened.** Reading twenty batch-1 exceptions by hand, as the gate asks. Three
were arithmetically correct and unreadable. They are `refund_timing_lag` orders: the
books have already written the order off to zero, and the platform paid it in full
because it has not deducted the return yet. The matcher compared 813.24 against 0.00,
found a fee variance, and said so.

**Why it mattered.** It is the difference between an exception a bookkeeper recognises
and one they have to reverse-engineer. It would also have poisoned checkpoint 3: those
rows carry the same reason code as the stale-rate rows while having nothing in common
with them, which is a rule induced on noise.

**Fix.** `paid_against_fully_reversed_order` — when the ledger expects nothing at all
from an order and the platform paid something, the finding is that the books are ahead of
the platform, not that the fee is wrong.

### 10. The greedy pass was unbounded while the exhaustive one was not

**What happened.** `subset_search.max_subset_size` bounded the exhaustive sweep and not
the greedy pass in front of it. A test with four ₹10 rows against a ₹40 shortfall got an
"explanation" of four rows from a search configured to name at most three.

**Why it mattered.** The bound is not a performance setting. An unbounded search finds
*some* subset summing to the credit and calls it the answer, and an invented explanation
in a reconciliation is worse than an unresolved one. Half-enforcing it meant the config
number said something the code did not do.

**Fix.** Both passes stop at `max_subset_size`. Past it, the group is reported with its
shortfall, its full candidate list, and `search_exhausted: true`.

### 11. The ledger has no order date, so the date window needs a proxy

**Not a bug — a gap in the data contract, recorded because it constrains the matcher.**
`internal_ledger` in the brief carries `order_value` and rates and no date at all. For an
order that has settled, the window is measured exactly, off the settlement row's own
`created_at`. For an order that has *not* settled — precisely the case the window exists
to judge — there is no order date anywhere, so the check falls back to the end of the
batch the order was booked in: the latest date it could have been created.

That is conservative in the right direction; it will call a settlement late before it
calls a normal cycle missing. The cost is that a genuinely missing settlement is not
named until roughly four batches after the sale. The three `missing_settlement_row`
orders that never get a recovery credit do reach `settlement_overdue_beyond_window`, just
later than a system with an order date would manage.

### Gate answers

All four numbers below now come from `make score`, which reproduces what the throwaway
script found while part A was being written.

**1. Is the match rate plausible?** Batch 1 matches 81% of its settlement rows (48 of 59),
inside the 60–85% band the checkpoint expects. The rate declines to 58% by batch 10, which
is the corpus behaving as designed rather than the matcher degrading: batch 10 receives
every cross-batch reversal and refund lag planted in batches 8 and 9 and emits none of its
own. `test_batch_one_match_rate_is_plausible` keeps the band honest.

**2. Does the silent-clear count sit near zero?** 48 of 371 injected rows land in
`matched` (12.94%), and "near zero" here means **zero troubles that fall outside a
configured tolerance** — asserted by
`test_nothing_is_silently_cleared_outside_a_configured_band`. Every one of the 48 is
inside a band on purpose:

- 40 `rounding_variance` rows, paise drift under the ₹1.00 rounding tolerance. These are
  in the dataset precisely so they cost nobody any attention; flagging them would be the
  failure.
- 8 `settlement_lag_crossing_batch` rows from batch 9, injected with `extra_lag_days: 7`.
  Amazon's own lag is 7–14 days, so those settlements land 14–21 days out — inside the
  21-day window. A settlement arriving on day 21 of a 21-day window is not late. Narrowing
  the window to catch them is the same sin as widening a tolerance to flatter a match rate,
  in the other direction, so the window did not move.

Before fixes 7 and 8 the count was 99. The 51 that moved were real misses.

**3. Do twenty flagged exceptions read like real problems?** Yes, after fix 9 — that
inspection is what produced fix 9. Batch 1's queue reads: five Myntra orders billed at
27.2% against 25% in the books (₹27–104 each, against a band of ₹1.32–5.01); two Flipkart
sales reported and paid ₹0.00 with a weight-dispute hold flag; one payout claiming
₹38,617.00 against a ₹36,918.96 credit with the single ₹1,698.04 row that does not fit
named; one ₹17,625.00 credit with nothing in any settlement report to explain it. All four
are things a bookkeeper would recognise on sight.

**4. Does the N:1 grouping tie out for the clean settlements?** Yes. 150 payout groups
across the ten batches, 147 of them tie out within the ₹1.00 tolerance. All three that do
not are the injected duplicates, and in every one the search named the single row the
credit does not cover — ₹1,698.04, ₹4,272.09 and ₹3,288.13, one residual row each, no
exhausted searches. Three further bank credits have no settlement group at all; those are
the `bank_credit_unmatched` injections. The widest group aggregates 72 settlement rows, so
the join is genuinely N:1 rather than a rename.


---

## Checkpoint 2, part B — Measurement Harness

### 12. The silent-clear count, as first designed, could not tell you anything

**What happened.** The first version of the metric was the count the checkpoint asks
for: injected troubles the matcher marked `matched`. It came back 48, and 48 is not a
number you can act on. Every one of those rows is inside a tolerance *by definition* —
that is what `matched` means — so a check of the form "was it inside the band?" is
vacuously true and a check of "is 48 near zero?" has no unit.

**Why it mattered.** The checkpoint calls this "the number that tells you your tolerance
band is wrong". A count cannot tell you that. A band is wrong when troubles are clearing
*just* under it.

**Fix.** The harness now reports two numbers beside the count: the largest deviation that
was cleared anyway, and — the useful one — the **tightest headroom**, the smallest gap
between a cleared row's deviation and the band that permitted it. `rounding_variance`
clears with as little as **₹0.16 of headroom under the ₹1.00 floor**: one injected paise
drift came within sixteen paise of firing. That is a real signal about a real band, and
it is invisible in the count. `settlement_lag_crossing_batch` clears at ₹0.00 deviation
with the full band unused — nothing to find on the money axis at all.

Neither number caused a threshold change. They are there so the next person to argue for
one has something to argue with.

### 13. The harness attributed both halves of an injection to the same order

**What happened.** `missing_settlement_row` deletes the settlement row — that absence *is*
the trouble — so attribution falls back to the verdict on the affected order. The fallback
looped over the injection's `affected_order_ids` and broke on the first hit, so both rows
of a two-row injection were scored against order #1. The confusion table reported all six
`missing_settlement_row` rows as `fee_variance_outside_tolerance` when three of them were
`settlement_overdue_beyond_window`.

**Why it mattered.** It made the harness wrong in the most expensive direction: it
reported a *worse* outcome than the matcher had actually produced, on the cause whose
whole point is that the row is gone. Had it erred the other way it would have flattered
the matcher, which is the failure this instrument exists to prevent — so the bug is worth
recording either way.

**Fix.** The lists are both written sorted and the injectors that delete a row append one
order per row, so equal lengths mean position *n* pairs with position *n*. The fallback
pairs by index when the lengths match, and takes the most severe verdict across the
injection's orders when they do not — blurring *which* order, never *whether*.

### 14. The review queue counted the same missing order six times

**What happened.** An order that goes overdue in batch 5 and is never paid is correctly
unmatched in batches 5 through 10: the ledger row is an input in each of those batches,
and every input row gets a verdict. The first harness summed those verdicts into the
review queue. Three genuinely missing settlements produced 39 queue entries and ₹25,000 of
double-counted impact, and the queue appeared to grow when it was only failing to shrink.

**Why it mattered.** Checkpoint 3 measures a review rate falling over ten batches. A
denominator that re-queues unresolved items every batch would have hidden real progress
under an accumulating backlog — or, tuned the other way, manufactured progress. Either
way the curve would have been about the counting rule rather than about the system.

**Fix.** `harness/aging.py`. A finding is **new** the first time a row carries a reason and
**aged** every batch after. The queue counts new findings; the report prints aged ones in
their own column; `EXCEPTIONS.md` itemises each finding in the batch it was raised and
tells later batches how many remain open. Impact is carried on new findings only.

The split lives in the harness, not the matcher: the matcher is a pure function of one
batch and cannot know it has seen a row before. The harness sees all ten and can.

### 15. Wall clock is a required metric and a determinism problem

**Not a bug — a tension the checkpoint sets up.** It asks for wall clock per batch and
records per second, and it asks for a deterministic artifact. Those cannot both be true of
the same numbers.

Resolved by segregation rather than by dropping one. Timings live in a single `timings`
block in `data/score.json` carrying `"reproducible": false`; everything else in the file
is byte-identical run to run, and two tests assert exactly that — one strips the block and
compares the rest, the other asserts no float appears anywhere outside it. The text report
prints timings at the top so the accuracy section below is diffable between runs.

### 16. Cost plumbing is exercised at non-zero even though production is zero

**Not a bug — a deliberate choice worth stating.** Nothing calls a model in this build, so
every cost the harness reports is ₹0.00. A cost path only ever run at zero is a path
nobody has checked, and it would get its first exercise in the same commit that adds the
model — at which point a wrong rate and a wrong token count are indistinguishable.

So `tests/test_harness_cost.py` drives the arithmetic with real token counts against the
real rates in `config/pricing.yaml`, including the case that motivated the design: cost is
divided by transaction count *before* rounding to paise, because rounding first quantizes
a genuine ₹0.44 run to ₹0.00 across 1,200 rows. `pipeline/llm/usage.py` holds the ledger
checkpoint 3 records into; `harness/score.py` already reads it and already scores
auto-resolution precision against the answer key over an empty proposal list.

Precision over zero attempts is reported as **undefined**, not as 100%. A precision of
1.0 over nothing is the most flattering possible way to say nothing happened.


### 17. Three gaps found by asking what checkpoint 3 would actually need

Checkpoint 2 passed its own done conditions before any of this was noticed. The gaps
were found by reading checkpoint 3's rule schema against a real `make score` run and
asking, field by field, where each value would come from.

**The review rate could not decline.** `review_rate` was a pure function of the
matcher's buckets, and the matcher does not change when a rule resolves a row. Checkpoint
3's headline done condition — "review rate declines across the ten batches" — had no
number that could move. Fixed by splitting it in two: `review_rate` stays the matcher's
own measurement and must not move, `net_review_rate` subtracts what learned rules
resolved and is the series the chart plots. Two columns rather than one on purpose: a
decline produced by widening a tolerance and a decline produced by learning are
indistinguishable in a single column.

**A lagged deduction carried no time.** `late_row_for_already_settled_order` is the
second largest reason code (99 rows) and covers two of the four recurring learnable
causes, and its verdict detail held only `row_net`, `type` and `description`. The rule
schema has a `lag_window_days` field with nothing in the corpus to fill it.

Worse than a missing field: in this dataset `refund_timing_lag` is injected only on
Amazon and `rto_reversal_later_cycle` only on Flipkart, so the two are perfectly
separable *by channel*. A rule induced as "amazon + refund → refund timing lag" would
have scored 100% on all ten batches while having learned the generator's injection plan
rather than the phenomenon. Exactly the confident nonsense the checkpoint gate warns
about, and the harness would have reported it as a success.

Fixed by carrying the settlement date of a closed order forward through `OpenBook`, so a
late row reports `days_after_settlement` (7–14 for refund lag, 7–21 for RTO) alongside
`days_since_order`. The two causes now overlap temporally, which is realistic and which
forces a rule to be about lag rather than about a channel.

**Rule bands were left to be re-derived downstream.** "Myntra is billing 27.2% against
our 25%" is a percentage, and the matcher was emitting only the rupee delta. Induction
and application would each have had to define "8.8% over" for themselves, in different
modules, from different fields. The matcher now emits `fee_variance_pct` and
`net_variance_pct` once — `None` rather than `0.00%` when the expectation is zero, since
a percentage of nothing is undefined and reporting it as zero would read as "no variance"
on the rows carrying the largest one.

The measurement that came out of it is the useful part. Per channel, `commission_rate_stale`
is a *point*, not a spread — 8.80% on every Myntra row, 7.50% on every Amazon row. The
held-out `promo_cofunding_deduction` sits at 25–40%, cleanly outside it, so correct
abstention in batch 7 is achievable rather than hoped for. And the near-miss sits at
exactly 8.80% on Myntra: inside the band, on the same channel, indistinguishable. Three
tests in `tests/test_learning_features.py` now hold all three of those properties in
place, because each is a dataset property that checkpoint 3 silently depends on and that
nothing else would notice breaking.
