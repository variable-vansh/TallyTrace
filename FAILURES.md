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
