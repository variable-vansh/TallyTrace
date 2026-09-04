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

---

## Checkpoint 3 — The Learning Loop

### 18. Hypothesis generation was designed per row, which would have made the cost report a lie

**What happened.** The first design asked the model about every queued case, as the
checkpoint describes: "for every `variance` and `unmatched` row, send the LLM the row".
Four hundred cases, four hundred calls. Then I looked at what the calls actually
*contained*: eighty-nine of them were "Myntra, fee variance outside tolerance, short,
8.8% over expectation" with nothing distinguishing them but the paise.

**Why it mattered.** Two ways, and the second is worse. The obvious one is cost: the
harness reports rupees per reconciled transaction, and asking the same question
eighty-nine times would have inflated that number by roughly an order of magnitude
while adding nothing. The subtle one is determinism. If two numerically identical rows
can get different hypotheses, the difference is noise, and it is noise that a
bookkeeper would read as a distinction.

**Fix.** The prompt is built from a normalised *question* — channel, reason code,
direction, variance percentages to one decimal, day counts as bands, row type — rather
than from the case. Identical questions collapse to one cached answer by construction,
because the cache key is a hash of the prompt. The corpus's 395 non-quarantined cases
ask **45 distinct questions**. The case's own exact rupee figures are shown beside the
hypothesis in the UI, straight from the matcher's verdict detail, where they are exact
rather than paraphrased.

### 19. The rupee guardrail was measuring the size of the sale, not the size of the error

**What happened.** `CaseFeatures.variance_inr` is the number `max_variance_inr` is
applied to. The first version took the largest money figure in the verdict detail,
which included `expected_net`. A ₹32.73 commission variance on a ₹3,917 order reported
a variance of ₹3,917.

**Why it mattered.** Every stale-rate exception in the corpus is a two-figure error on
a four-figure order. Under the ₹500 ceiling, the guardrail would have refused to
auto-resolve **every single one** — and it would have looked like conservatism working
rather than like a bug. The auto-resolution rate would have been near zero and the
explanation would have been "the guardrails are strict", which is exactly the kind of
wrong answer that sounds like a good one.

**Fix.** `DELTA_KEYS` names the four fields that measure a *deviation*;
`expected_net` is the fallback and only the fallback, for an order that never settled
and therefore has no delta. `test_the_guardrail_number_measures_the_error_not_the_order`
holds it.

### 20. Two rules were permanently in conflict because rules could not see a row type

**What happened.** `late_row_for_already_settled_order` covers two different
phenomena that arrive days apart and both take money back: a refund deduction landing
a cycle late, and a TCS recovery landing a cycle late. The rules induced for them were
`(amazon, late row, short, 1–14 days)` and `(amazon, late row, short, 1–7 days)` —
equally specific, different causes. `select` correctly refused to choose and sent
every Amazon late row to a human.

**Why it mattered.** The refusal was *right* — equally specific rules that disagree
should go to a person — but the underlying problem was that the rule schema could not
express a distinction the operator's own note made explicitly ("this is the TCS they
didn't collect at the time of the sale", versus "we issued this refund ourselves").
The system was less expressive than the sentence it was learning from.

**Fix.** `transaction_type` added to the induced-rule schema, the stored rule, the
predicate and the specificity count. It is a property of the phenomenon and not an
identifier, so it does not weaken the no-memorisation constraint. The two rules now
have disjoint conditions rather than a permanent tie.

### 21. The review rate does not decline to batch 10, and I did not fix the data

**This is the one done condition this checkpoint does not meet, and the interesting
part is what fixing it would have required.**

The net review rate runs `18.64 → 22.67 → 18.39 → 25.49 → 21.93 → 12.50 → 14.89 →
15.48 → 17.86 → **22.65**`. It falls hard through the middle of the corpus and ends
four points above where it started.

**Why.** Batch 10's queue is 76 flagged settlement rows, of which 33 are late
deductions — RTO reversals and lagged refunds — every one of them a four-figure
clawback. The `max_variance_inr: 500.00` guardrail refuses to auto-resolve any of
them. Rules R-11 and R-17 match all 33 and explain all 33 correctly; the ceiling holds
every one for a human anyway.

Batch 10 has three cohorts of them because the injection plan compresses the
cross-batch offsets near the end of the corpus so the rows still land inside it —
batch 8's, batch 9's and batch 7's reversals all arrive in batch 10. That is stated in
`config/generation.yaml` and was already flagged in failure #6 as the first thing to
check if batch 10 looks unlike the rest.

**Three ways to make the number decline, and why I took none of them.**

1. *Raise `max_variance_inr`.* This is the one that works instantly and it is
   explicitly what the checkpoint says not to do. A ₹2,400 clawback against an order
   the books had closed is money at risk, and a rule being confident about it does not
   make it less so.
2. *Exclude late deductions from the variance measure*, on the argument that the money
   is correct and only the cycle is wrong. That argument is genuinely available — it
   is the argument failure #7 accepted for `settlement_outside_date_window`, which
   carries an impact of ₹0.00. It does not hold here: a late *payout* matched the
   books, whereas a late *deduction* has no counterpart in the books at all. Until
   someone confirms the return happened, the whole amount is unexplained.
3. *Thin out batch 10 in the generator.* Making the last batch cleaner to flatter the
   curve is the same failure as making batch 1 cleaner, which checkpoint 1 forbids by
   name. It would also have been the most invisible of the three.

**Since.** The ceiling is now a number the business sets — a default plus per-cause and
per-channel ceilings in `config/thresholds.yaml` — which does not change the reasoning
above so much as say whose reasoning it is. Option 1 was wrong for *me* to take
unilaterally to make a curve decline; it is a perfectly legitimate thing for a finance
lead to decide about their own money, in the open, with the number and their name
attached to every resolution it closes. `make whatif ceiling=3000` prints what this
corpus would have done: 233 auto-resolutions instead of 146, ₹158,337 instead of ₹8,203,
at 99.14% precision instead of 98.63% — with almost all of the movement in exactly the
batch-10 clawbacks described above. That the precision *rises* is the strongest
available argument for raising it, and it is still not an argument I get to accept on
someone else's behalf. The shipped default is unchanged at ₹500, and a what-if writes
no artifact, so nothing on this page or in `RESULTS.md` describes a tuned run.

**What I did instead.** Reported two series and printed both, with the arithmetic for
each. `net_review_rate` is rows a human still owns; it is the strict reading and it
does not decline. `human_touchpoints` is *distinct decisions a human has to make* —
a case no rule matched counts once, and a batch proposal card counts once no matter how
many rows it collapses. That series runs `22.03 → 22.67 → 16.09 → 25.49 → 25.44 →
14.06 → 10.64 → 9.68 → 9.52 → **6.08**`, a 3.6× decline that plateaus above zero.

Both numbers are true and they measure different things. Batch 10 leaves 41 rows with
a human and asks them 11 questions. Reporting only the second would be the failure this
harness exists to catch; reporting only the first would hide the entire point of the
batch-proposal design. So the report prints them side by side and says which is which.

### 22. The near-miss fooled the operator too, so one precision number was not enough

**What happened.** The near-miss rows are Myntra orders repriced to exactly the
stale-rate signature — same channel, same 8.80% band — with a different true cause in
the answer key. Rule R-05 fires on them and is wrong, which is what they are for.
Then the *operator* looked at one, saw a Myntra order 8.8% over the master rate, and
wrote the stale-rate note. So the rule's live precision, computed from what the human
said, stayed at 100.00%.

**Why it mattered.** Live precision is the product's signal: it is how the system finds
out it was wrong without an oracle. But an operator and a rule can be wrong in the same
direction, and if the only number on the rules page is the one they agree on, the
system reports perfect confidence about a row it got wrong.

**Fix.** Two precision numbers, printed adjacent. **Live precision** (100.00% for R-05)
is what the operator's resolutions said. **True precision** (97.44% over 78) is what
the answer key says about the rows the rule closed unattended. The rules page shows the
gap and says in words what it means. Overall auto-resolution precision is **98.63% over
146 scored resolutions**, and the two rows separating it from 100% are exactly the two
`near_miss_fee_variance` injections, in batches 5 and 8.

The done condition asks that the near-miss "either got caught by a guardrail or shows
up in the precision number as a real miss". It shows up, twice, and
`test_the_near_miss_shows_up_as_a_real_miss_rather_than_passing_silently` fails if it
ever stops.

### 23. The token counts are estimated, and every number built on them says so

**Not a bug — a provenance statement.** There is no `ANTHROPIC_API_KEY` in the
environment this was built in, so the cached answers in `data/llm_cache/` were produced
by Claude Opus reading each rendered prompt through a coding session rather than
through the HTTP Messages API. The request text and the output schema are byte-identical
to what `pipeline/llm/client.py` sends; only the transport differs.

The consequence is token counts. The API meters them and a transcript does not, so
those entries carry `source: "transcript"` and their usage is derived from character
length via `estimated_chars_per_token` in `config/pricing.yaml`. The alternative —
recording zero — would report a model-backed pipeline as free, which is a more
misleading number than an approximate one.

`LlmClient` tracks whether any answer it billed was estimated, and the throughput
section of `make score` prints **TOKEN COUNTS ARE ESTIMATED** in full whenever it was.
Set a key, delete `data/llm_cache/`, run `make llm-fixtures`, and the same prompts go
over the wire; the cache repopulates with `source: "api"` and metered usage, and no
other code changes.

One related decision: a cache hit is billed at the **cache-read** rate rather than as
free. The first run paid for the answer, and a per-transaction cost that only counts
the runs where the disk happened to be empty is not a cost. Current figure: 337,024
tokens, ₹141.51, **₹0.117 per settlement row**.

### 24. The rule that retired itself was left in, and the note that caused it was not rewritten

**Deliberate, and it is the single best piece of evidence in the build.**

In batch 2 the operator wrote, about a Flipkart clawback: *"A deduction landing a week
after we'd already been paid and closed the order. That's a return coming back through
— happens on all the marketplaces, the goods take time to get to the warehouse."*

That sentence generalises across every channel, and induction faithfully produced R-07:
no channel, late-row reason, refund type, 1–21 day lag, cause
`rto_reversal_later_cycle`. In batch 3 it predicted on six late rows: three Flipkart
(right) and three Amazon (wrong — those are refunds the seller had already booked, as
the operator's own Amazon note says). Five judged observations at 40.00% precision,
below the 75.00% floor, and `advance` retired it at the end of batch 3 with the reason
recorded on the transition.

It would have been trivial to write a tighter batch-2 note. That is precisely the thing
the checkpoint warns against — "if you write resolution text engineered to induce
cleanly, you have tested nothing" — and it would have removed the only demonstration
that retirement happens. The rules page shows R-07 in red at the top with an
explanation rather than filtering it out.

### 25. The ledger rate correction is proposed, not applied to the corpus

**A decision, recorded because it is a real limitation.**

R-05's action is `update_ledger_rate → expected_commission_rate = 0.242`. Applying it
would be the honest production behaviour: correct the master rate and the exception
stops being generated at all.

It is not applied to the ten batches, and the reason is measurement. The corpus is a
fixed historical extract that the harness scores against a fixed answer key. If
accepting the rule in batch 3 rewrote the ledger for batches 4 to 10, those Myntra
orders would reconcile clean — and the harness, which knows from `data/truth` that
those rows carry an injected `commission_rate_stale`, would score all 127 of them as
**silent clears**. The single most important honesty metric in the build would report a
catastrophe caused by the system working correctly.

So the action is recorded on the rule, shown on the proposal card and on the rules page,
and left unapplied against the measured corpus. A production deployment writes it back;
a benchmark cannot mutate its own input and still be a benchmark. Stated here rather
than buried, because "the rate fix doesn't actually fix anything yet" is a fair question
to ask of this build and it deserves a real answer.

### 26. Three checkpoint-2 tests were describing seams that no longer exist

`test_precision_over_no_attempts_is_undefined_not_perfect` asserted
`score.proposals == []`. `test_the_net_review_rate_falls_when_a_rule_resolves_rows`
passed `proposals=` into `harness.score.run`, which now derives them. Both were correct
about checkpoint 2 and wrong about the repo.

Neither was deleted. The first was rewritten to assert the property on
`auto_resolution_precision([])` directly, where it still holds and always will, plus a
new test that the seam is now *filled* — because an empty proposal list would make every
precision number in the report read "undefined" rather than "wrong", which is the quiet
regression the harness is for. The second was rewritten to drive `batch_metrics` with a
hand-built resolved set, so it keeps testing the arithmetic as the loop that feeds it
changes.

`test_the_answer_key_is_read_in_exactly_one_module` also failed, because
`harness/learning.py` mentioned `data/truth` in its docstring. The assertion is worth
more than the sentence, so the docstring changed and the test stayed strict.

### 27. The card-decision path is real code the shipped run never exercises

The checkpoint asks that "Not this time" record a negative observation against a rule,
affecting its precision and possibly retiring it. `_apply_card_decisions` does exactly
that, and in the shipped run it does nothing at all, because the operator declined no
cards: the one retirement came from their own later resolutions contradicting an
over-general note.

I could have planted a decline to make the path light up. It would have meant inventing
an operator who rejects a card whose cause is correct, which is a worse thing to put in
the data than an unexercised branch is to put in the code.

Instead the path is driven directly by four tests — accept confirms every prediction
behind the card, decline refutes them and the record then clears the retirement floor,
"review individually" judges nothing either way, and a decision only touches the batch it
was made in. Recorded here because "why does this branch have no coverage from the run?"
is a fair question and the answer is a choice rather than an oversight.

### 28. The UI implies a write-back it does not have

The proposal cards carry "Accept all", "Review individually" and "Not this time"; the
rules page carries "Narrow the band" and "Disable". There is no server behind the UI —
it renders one scored run from a JSON file — so none of them writes anything.

Left in rather than removed, because they are the interaction the checkpoint is about
and a rules page with no controls would misrepresent the design in the other direction.
Made honest instead: the queue carries a sentence at the top saying this is a recorded
run, and each control, once clicked, states exactly what it *would* record — "would
record 3 negative observation(s) against R-27, lowering its live precision and possibly
retiring it". The logic behind each is real and tested; only the persistence is missing.

### 29. Three things the wrap-up audit found that the tests were happy with

Read the whole tree back against the brief's quality bar before committing. Three real
defects, none of which any test noticed.

**A dead branch in the precision scorer.** `true_cause_of` read:

```python
return sorted(found)[0] if len(found) == 1 else (sorted(found)[0] if found else None)
```

Both branches are identical. It looks like it handles the ambiguous case — a case whose
rows map to two different injected causes — and it does not; it takes the
alphabetically first cause either way. There are 0 such cases in 390 attributable ones,
because no injector stacks two troubles on one order, so the branch never ran and
nothing failed. But an arbitrary pick would have flattered or penalised the run
depending on nothing, and the *shape* of the code claimed a care it did not take.

Now returns `None` on ambiguity — refusing to score is the honest answer, and the count
already surfaces as `unscored_auto_resolutions`. The docstring states the measured
figure so the next reader knows the branch is dead by construction rather than by luck.

**The boundary greps did not cover `tools/`.** `SOURCE_DIRS` was written in checkpoint 2,
when `tools/` did not exist. It now holds the fixture writer, which builds the same
prompts the client sends and is the single most obvious place to put a shortcut that
calls a model directly and skips the cache. The `anthropic` and fuzzy-matching greps
now include it.

The same read produced a *tighter* test rather than a looser one. There was an assertion
that `harness/truth.py` is the only module reading the answer key, and no equivalent on
the writing side. `test_only_the_generators_entry_point_names_the_truth_path` now asserts
`generator/main.py` is the only module that names it going in — so "who could have
touched the answers?" has a two-line answer instead of a one-line one.

**A module with no tests.** `pipeline/rules/proposals.py` — the batch proposal cards,
which are the entire reason the two review series in the report diverge — had only
end-to-end coverage. The brief's bar is "every module gets tests before the next
checkpoint starts", and a card that could claim rows its rule did not match, or merge an
auto-resolution with a guardrail hold, would turn the touchpoint number from a
measurement into a story. Nine tests now, including the one that matters most: resolved
and held rows from the same rule get separate cards.

Also added `tests/test_ui_data.py`, which asserts the browser and the terminal are
quoting the same run, and that `data/score.json` contains no float outside the labelled
`timings` block.

### 38. Four defects a review pass found, three of them only visible on screen

I had shipped checkpoint 4 with tests green, mypy clean and `make reproduce` identical,
and had never once looked at the UI in a browser. Driving headless Chrome at it found
three things no test was ever going to catch, and one that a test should have.

**Every chart rendered with no line and no bars.** Recharts animates marks in on mount,
and the animation never completes under a headless capture — axes, gridlines and dots
drew, the paths did not. In a live browser it is fine, so this was invisible from the
terminal and would have been invisible right up until the screen recording. Every chart
in the app now sets `isAnimationActive={false}`: an entrance animation on a
reconciliation dashboard buys nothing and costs a flash of empty axes on every tab
switch.

**The claim recovery rate reported website at 0.00%.** Six website chargebacks are open
and not one has settled either way, so the denominator is zero — and `_pct` returns zero
for zero over zero, which is right for a count and wrong for a rate. A bar reading "0%
recovery on website" states a failure where there is only an unfinished filing window.
Platforms with nothing settled are now omitted rather than plotted, which is what a bar
chart's version of "undefined" looks like.

**Three screens showed corpus-wide numbers under a week selector.** The claims register,
the take-rate charts and the dashboard's claims panel are all whole-corpus, and the page
headers said "Week 1 · 2025-01-06 → 2025-01-12" above them. Nothing was wrong with the
numbers and everything was wrong with the label. Each card now says which it is, and the
claims screen says why it does not follow the selector: a filing window does not reset
because you are looking at an earlier week.

**A windowed series could report a batch outside its own window.**
`rupees_expired_unrecovered` seeds its buckets from the requested window but was
accumulating by the *expiry* transition's batch, and a claim opened in the window can
expire outside it. Nobody hit it because the shipped views ask for the whole corpus. Now
guarded, with a test that asks for batches one to five and asserts it gets five bars.

### 39. The ask surface was a log of questions, not a place to ask one

The reporting surface shipped as a static list: here are eleven questions somebody
asked, here is what the system said. Every claim in the README was true of it and it
demonstrated none of them, because the thing worth showing — the restatement appearing
*before* anything is computed, and a human accepting it — is a sequence, and a table
cannot show a sequence.

It is now a conversation on its own screen, with the pinned board beside it. You type,
it restates and stops, you accept, the chart appears, a button pins it. The clarification
turn hands you the registry to answer with, which is a better answer than I originally
had: the model asked the question and the registry is the vocabulary the answer has to
come from, so no second model call happens.

Two things had to be built to make it honest rather than a mock. The browser cannot run
the registry — the metrics are `Decimal` arithmetic in Python — so every metric at every
grouping it supports is precomputed into the UI payload and the screen renders a lookup.
That is not a shortcut around the design; a metric is a pure function of the corpus, so
its value is settled the moment the batch is scored. And a question outside the committed
fixtures returns "I have not been asked this one" rather than anything resembling an
answer, because the alternative is a chat box that appears to understand English and
does not.

### Checkpoint gate — done conditions, answered

| condition | result |
|---|---|
| Review rate declines **and** precision holds | **Partial.** Precision holds at 98.63%. The strict row-level rate ends 4 points above batch 1; the decision-level rate falls 22.03% → 6.08%. Failure #21 has the full argument and the three fixes I refused. |
| The curve plateaus above zero | **Yes.** No batch reaches zero on either series; batch 10 still asks 11 questions. |
| Held-out categories in batches 7 and 9 correctly not auto-resolved | **Yes.** 100% abstention on both, on first sight and across the whole corpus. Neither is a special case in the code — it falls out of the lifecycle and the guardrails. |
| At least one rule retired itself, and you can point to why | **Yes.** R-07, batch 3, 40.00% over 5 judged observations. Failure #24. |
| The near-miss caught by a guardrail or visible as a real miss | **Visible as a real miss**, both of them, and they are the entire gap between 98.63% and 100%. Failure #22. |
| No rule in the store contains a transaction id | **Yes**, enforced twice — the schema has no field for one, and `assert_generalisable` checks the free-text values. Four tests, including one over the shipped `data/rules.json`. |
| Provenance chain complete for every auto-resolution | **Yes.** Rule, state at fire, source resolution, operator, proposed cause, and all three guardrail evaluations on all 146. The decision-path view renders the record verbatim. |
| LLM calls confined to `pipeline/llm/`, cached, deterministic on rerun | **Yes.** `anthropic` is imported in exactly one file; `pipeline/rules/` cannot reach a client at all; `score.json` is byte-identical across runs apart from the labelled `timings` block. |
| Batch proposal cards and rules page plugged into the existing UI | **Yes.** Both in the existing shell, plus the decision path on every exception and every flagged transaction row. |

---

## Checkpoint 4 — Claims, Reports, Packaging

### 30. The effective take rate climbed from 5% to 86% and none of it was real

**What happened.** The first version of `pipeline/metrics/corpus.py` took gross order
value from the batch's `internal_ledger.csv` and every deduction from the batch's
`settlement_report.csv`. The resulting take rate per batch read
`5.27, 12.13, 16.71, 16.01, 15.44, 16.17, 20.07, 23.59, 40.10, 86.53`.

**Why it mattered.** It looked, at a glance, exactly like the signal the chart exists to
catch — a take rate climbing week over week is what a silent commission change looks
like from the outside — and I nearly wrote a paragraph about it. It is an artifact. A
batch *is* a settlement report: its ledger file holds the orders booked that week and its
settlement rows hold the orders paid that week, and those are different sets. Batch 10
absorbs every late settlement in the corpus and books almost nothing, so the numerator
was near its maximum over a denominator near its minimum. Numerator and denominator were
drawn from different populations, which means the quotient was not a rate.

**Fix.** Gross order value is now looked up per *settling* order, from an index built
across every batch's ledger, and deduplicated — an order that emits a payment and a
refund in the same batch contributes its value once. The series is now
`18.03, 15.98, 19.61, 19.04, 17.24, 16.81, 18.98, 17.72, 16.69, 15.61`, which is a flat
line, which is the truth about this corpus. `test_the_take_rate_denominator_is_the_orders_the_rows_settle`
asserts every batch stays inside 5–40% so the old shape cannot come back quietly.

The near-miss is the point of the entry. A wrong chart that looks boring gets checked. A
wrong chart that looks like the finding you were hoping for does not.

### 31. The claims queue opened a claim on the credit that closed a claim

**What happened.** Routing sent every case whose cause carries `counterparty_claim` to
the register. The reimbursement rows the generator plants — the ones checkpoint 1 put in
specifically so that checkpoint 4's auto-close would have something to close — surface as
`late_row_for_already_settled_order` and get hypothesised as `short_payment_unexplained`,
because from the row alone that is what money moving against a closed order looks like.
So `ord_000081`'s reimbursement in batch 4 opened a second claim for the same ₹287.97 the
batch-2 claim was already chasing.

**Why it mattered.** Double-counted rupees in the queue header, and a claim drafted
against Flipkart asking them to pay money they had just paid.

**Fix.** A case whose direction is `over` never opens a claim: money arriving is not a
debt. The first version of the filter was `direction == "short"`, which was worse — it
silently dropped every `missing_settlement_row` claim, because an order that never
settled has no delta to take a direction from and comes through as `flat`. The filter is
`direction != "over"` and both halves are tested.

### 32. Matching recoveries on the description would have scored perfectly and meant nothing

**What happened.** The planted reimbursement rows carry
`description = "CLAIM REIMBURSEMENT ord_000081"`. Matching on that string is one line of
code and closes five of five planted pairs.

**Why it mattered.** It is not a reconciliation, it is a detector for one generator's
phrasing. No platform writes that string, and the number it produces would be a
measurement of the test fixture.

**Fix.** Recovery is an exact key plus an explicit tolerance band, the same as every other
match in this repo: same `order_id`, money in, amount within `rounding_tolerance_inr` of
the amount claimed. It closes three of the five planted pairs rather than five, and the
harness reports the other two as misses with the reason — in both, the reimbursement
arrived while the order was still inside its settlement window, so the matcher never
raised it and no claim was ever opened to close. A claim the system had no cause to open
is not a claim it failed to recover, and it is still reported as a miss, because
excluding it would be marking its own homework.

### 33. The claims register scored TCS discrepancies as 100% wrong for being exactly right

**What happened.** `harness/claims.py` scored every opened claim by asking whether the
answer key's cause for its rows was a `counterparty_claim`. TCS timing mismatches are in
the register for their GSTR-8 cutoff and nothing else — they are `tax_review`, they are
never drafted and never auto-closed — so both of them scored as false claims and the
attribution table showed `tcs_timing_mismatch  2  0  0.00%`.

**Why it mattered.** A metric that penalises correct behaviour will eventually get
"fixed" by changing the behaviour.

**Fix.** A claim is scored against the class it was *opened under*: a counterparty claim
is confirmed when the key agrees somebody else owes the money, and a TCS discrepancy is
confirmed when the key agrees it is a tax-review item. Both now score 100%.

### 34. Drafting a claim moved it backwards from filed to drafted

**What happened.** `ClaimRegister.advance` ran recover → expire → open → file, and
drafting was a separate call afterwards. In batches 1–3 the operator works the whole
queue, so a claim opened and worked in the same week ended the batch as `filed` and was
then moved to `drafted` by the drafting pass.

**Why it mattered.** The status a human sees would have been a week behind the work they
had already done, and the transition log — which is the claim's audit trail — recorded a
state change that never happened.

**Fix.** Drafting moved inside `advance`, between open and file, and the drafter is
injected as a callable rather than called directly. Two things fell out of that: the
order is now stated once in the module docstring instead of being implicit across two
call sites, and `pipeline/claims/` cannot construct a model client at all — asserted by
`test_the_claims_register_never_calls_a_model_itself`, alongside the same assertion for
`pipeline/metrics/`.

### 35. The test that proves the model wrote no numbers was itself unable to prove it

**What happened.** `ClaimNarrative` forbids the model a numeral, so every figure in a
finished draft must have been substituted from the matcher's verdicts. The first version
of the test built a set of "allowed" strings out of the claim's own fields and asserted
each numeral-bearing line contained one. It failed on
`Net received                      ₹0.00` — a figure that is entirely legitimate and
comes straight from the verdict detail of a fully withheld payout.

**Why it mattered.** The test was checking a proxy for the property rather than the
property. It would have passed a draft containing an invented figure on a line that also
happened to mention the claim id.

**Fix.** The test now rebuilds the draft's context the way the renderer does — from the
case's merged verdict detail — and asserts every *numeric token* in the whole draft
appears in it. Two follow-ons: the token regex first swallowed the comma in
`st_000868, st_000824` and had to be tightened to Indian-grouping shape, and the check
now covers the subject and the signature block rather than only the evidence table.

### 36. A docstring broke the answer-key boundary test

**What happened.** `harness/claims.py` explains where the planted recovery pairs come
from, and the sentence named `data/truth/manifest.json`.
`test_the_answer_key_is_read_in_exactly_one_module` greps for the path rather than for an
import and failed: `the answer key is read in: ['claims.py', 'truth.py']`.

**Why it mattered.** It is a false positive — the module receives an already-loaded
`AnswerKey` and opens nothing — but the test is right to be blunt. A grep that exempted
comments would have to decide what a comment is, and the boundary is worth more than the
convenience of naming a path in prose.

**Fix.** Reworded the docstring to say "the answer key's manifest" and to state which
module does own the path. The test stayed as it was.


### 40. Raising the ceiling improved the precision the product can see and wrecked the one that is true

**What happened.** Asked to try `max_variance_inr: 1000.00` and keep it if it came out
better, I ran it and it looked better on every number in the report: 162 auto-resolutions
instead of 146, ₹19,581 closed instead of ₹8,203, the last batch's review rate down from
22.65% to 19.89%, and **auto-resolution precision up, 98.63% → 98.77%**. I was one commit
from shipping it.

**Why it mattered.** The 98.77% is *live* precision — a rule judged against the cause the
operator's own words imply. It is the product's signal and the honest one to build on,
because it is how the system finds out it was wrong without an oracle. It is not the same
number as the harness's, which scores the same resolutions against the answer key the
pipeline never reads. At ₹500 the two are identical. At ₹1,000 the true figure is
**95.68%** — seven rows closed with the wrong cause instead of two.

The report prints both, and I read the wrong one first because it was the one in the
learning section next to everything else that had improved.

**The curve, from `make ceilings`:**

| ceiling | closed | wrong | true % | live % | gap |
|---|---|---|---|---|---|
| ₹500 | 146 | 2 | 98.63 | 98.63 | 0.00 |
| ₹600 | 149 | 2 | **98.66** | 98.66 | 0.00 |
| ₹700 | 155 | 4 | 97.42 | 98.71 | 1.29 |
| ₹1,000 | 162 | 7 | 95.68 | 98.77 | 3.09 |
| ₹2,000 | 202 | 21 | 89.60 | 99.01 | 9.41 |
| ₹3,000 | 233 | 30 | 87.12 | **99.14** | 12.02 |

Live precision rises monotonically with the ceiling and true precision falls. That is not
noise, it is failure #22 at scale: the marginal rows are ones a rule and an operator get
wrong in the *same direction*, and the bigger the row the more often they agree wrongly.
A ceiling chosen on live precision alone rises forever, and every step of the way the
system reports that it is getting better at it.

**Fix.** `tools/ceiling_sweep.py` scores the corpus at every candidate ceiling and prints
both series side by side, and the threshold control on Report & Settings will not render
one without the other. The ceiling stayed at ₹500. ₹600 is the frontier — it closes three
more rows with the same two errors and the two measures still agree — and it is left as
the operator's call rather than taken as mine, which is the whole point of having made the
number settable.

**What I would have shipped otherwise.** A guardrail loosened by 2×, five more wrong
resolutions in the corpus, and a README claiming precision had improved. Every number in
that claim would have been real.

### 37. Two things the checkpoint-4 audit found that the tests were happy with

**A claim could in principle be opened and closed in the same batch.** Ordering inside
`advance` prevents it — recovery runs before opening — but nothing said so. If the order
were ever changed, a claim raised on Monday could be marked recovered on Monday by the
very row that raised it, and the recovery rate would climb for no reason. `_recover` now
filters explicitly on `claim.opened_batch < batch`, and
`test_a_claim_never_opens_and_closes_in_the_same_batch` asserts it over the shipped run.

**`written_off` is a status nothing in this corpus reaches.** It is in the spec's claim
schema and it is implemented, and no claim in ten batches gets there, because writing off
a claim needs an operator action the operator log has no record of. Left in and said
plainly here rather than deleted, because the alternative — quietly narrowing the status
set to the ones the demo happens to exercise — is how a schema stops describing the
domain and starts describing the fixture.

### Checkpoint gate — done conditions, answered

| condition | result |
|---|---|
| `make demo` reproducible from a clean clone, offline, identical numbers twice | **Yes.** `make reproduce` runs it twice and diffs four artifacts; all four hashes match. `--offline` refuses the network *with a key present*, so what is proved is that the fixtures and the seed suffice. |
| Claims auto-close against planted recovery credits | **Partly, and the gap is reported.** 3 of 5 planted pairs auto-close. In the other two the reimbursement arrived before the settlement window elapsed, so no claim was ever opened; both are listed as misses in the recovery table rather than excluded. Failure #32. |
| Claims queue sorted by expiry with the summary header | **Yes.** `₹35,252.95 open across 20 claims · 3 expiring in 9 days`, sorted by deadline with unclocked claims last. Same view in `make claims`, in EXCEPTIONS.md and in the UI. |
| Pinned metrics recompute with no LLM call | **Yes**, and asserted rather than asserted-in-prose: `tests/test_pins.py` monkeypatches `LlmClient.__init__`, `LlmClient.ask`, `client_from` and `ResponseCache.get` to raise, then recomputes all five pins. |
| Registry refuses unmappable questions instead of guessing | **Yes.** 8 of 11 logged questions map, 1 clarifies, 2 refuse. The schema rejects an outcome that refuses and names a metric at the same time, so a refusal cannot carry a chart. |
| Every number in the README traced to a run | **Yes**, and enforced: `tests/test_readme.py` checks the opening claim, the headline table, the benchmark row, the review-series endpoints and the ₹34-lakh gap against `data/score.json` and the metric registry. It also fails if the stated test count is wrong. |
| `EXCEPTIONS.md` and `FAILURES.md` real and non-empty | **Yes.** EXCEPTIONS.md is 666 itemised findings plus the open and expired claim registers; FAILURES.md is 40 entries kept since checkpoint 1, none reconstructed. |
| Architecture diagram organised on the AI boundary | **Yes.** Four shaded nodes, everything else labelled "deterministic by choice", mermaid plus an ASCII fallback. The mermaid source was parsed with mermaid 11 before committing rather than eyeballed. |
| Video recorded, ending on the unresolved exception | Recorded separately from this repo. Every figure it quotes is one `make score` prints, and the ending is the one this file and `EXCEPTIONS.md` already carry: eleven claims expired unrecovered, and a bank credit nobody can explain. |
