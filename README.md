# Tallytrace

A reconciliation agent for a multi-channel Indian apparel seller (Amazon, Flipkart,
Myntra, own website, offline POS). It reconciles three sources, surfaces what it cannot
match, learns from how a human resolves those exceptions, and applies what it learned to
later batches so the manual queue shrinks.

**Status: checkpoints 1 and 2 complete** — the data foundation, the deterministic
matcher, and the measurement harness. The learning loop (checkpoint 3) and the claims
queue (checkpoint 4) are not built yet. No LLM is called anywhere in this build.

---

## Quick start

```bash
make venv           # .venv + pinned dependencies
make generate       # ten batches + ground truth, seeded and reproducible
make reconcile      # run the matcher across all ten batches
make score          # score it against the answer key; writes EXCEPTIONS.md
make check          # clean-base verification, tests, mypy
```

`make generate` prints, per batch, how many troubles were injected, of which causes,
worth how many rupees.

---

## What is in the data

Ten weekly batches built as **clean base + injected troubles**, with a separate
ground-truth key recording every injection.

| | |
|---|---|
| Settlement rows per batch | 59 → 181, monotonic, every batch over the 50-record floor |
| Bank credits | 150, largest aggregating 59 settlement rows (a real N:1 join) |
| Ledger rows | 1049 orders |
| Injected troubles | 371 affected rows across 16 causes, ₹4.6L of true impact |
| Malformed rows | 5, spread across batches 2, 4, 6, 8 and 9 |

### Layout

```
config/     thresholds.yaml, causes.yaml, channels.yaml, generation.yaml
data/
  generated/  batch_01 .. batch_10, three CSVs each
  truth/      the answer key — PIPELINE MUST NEVER READ THIS
generator/  synthetic world, injectors, writer, clean-base verifier
pipeline/   models.py, config.py, loader.py, run.py + matcher/
harness/    scoring: reads data/truth, which the pipeline never does
tests/
```

---

## Design decisions worth knowing before checkpoint 2

**The matcher must read the ledger cumulatively.** A settlement row lands in the batch
its payout fell into; a ledger row lands in the batch the order was booked in. An order
booked in batch 3 and paid in batch 5 therefore appears in two different files. This is
the point — it is what makes cross-batch settlement lag real rather than a story — but it
means reconciling batch N requires ledger rows from batches 1..N, not batch N alone.

**Batch size means settlement rows.** That is the table whose rows get bucketed as
matched / variance / unmatched / quarantined, and the denominator the review rate is a
percentage of. Total records per batch (all three tables) is larger, between roughly 220
and 250.

**The corpus is closed.** Every order generated settles inside the ten batches, which is
what lets the clean base reconcile at exactly 100%. The cost is at the edges: batch 1
carries a large opening book (173 ledger rows created before the corpus starts), and
batch 10's ledger is small (24 rows) because an order booked in the final week has no
later cycle to settle in. Cross-batch causes are correspondingly thin at both ends — see
FAILURES.md #6.

**Money is `Decimal`, everywhere, and floats are refused rather than converted.**
`pipeline/models.py` raises on a float in any money field, on construction and on
assignment. Config YAML floats are parsed as `Decimal` too, so a commission rate cannot
enter the system as binary floating point. Rounding is half-up, not banker's.

**The clean base reconciles at 100% before anything is injected.** `make verify-clean`
proves it: every booked order settles for exactly what the books expect, every fee equals
the ledger's own derivation, no settlement row points at an unknown order, and every
payout group ties to exactly one bank credit. It is written against the emitted world
rather than reusing the code that built it, so a builder bug cannot cancel itself out. If
this does not come back clean, nothing downstream is worth debugging.

**The ground truth records what was done, not how hard it is.** Each entry holds the
affected row ids, the true cause, the true rupee impact and the correct resolution class.
It records no claim about whether a matcher *should* catch it — that is the harness's
finding, not the dataset's assertion. A test asserts the entry keys stay exactly that.


---

## The matcher

`make reconcile` walks the ten batches in order and buckets every row. Batch 1 matches
81% of its settlement rows; batch 10 matches 58%, because it receives every cross-batch
reversal and refund lag planted in batches 8 and 9 and emits none of its own.

### Four buckets, one reason code each

Every input row lands in exactly one of `matched`, `variance`, `unmatched` or
`quarantined`, with a machine-readable reason code saying which check produced the
verdict. `assert_one_bucket_each` enforces the "exactly one" in code, and a test asserts
the row count going in equals the row count coming out, per table, per batch.

Reason codes are not causes. A cause is what the answer key says was done to the data; a
reason is what the matcher observed. Keeping them apart is what lets the harness ask
"which bucket did cause X land in?" and get a real answer instead of a tautology.

### No fuzzy matching. Anywhere. On purpose.

Order level is an exact key. Bank level is an exact key plus an explicit rupee tolerance.
There is no similarity score, no probabilistic linkage, no `recordlinkage`. In finance a
0.87-confidence match is not a match, it is a liability: it books money against an order
nobody chose to book it against, and the audit trail reads "the algorithm was fairly
sure". `test_matching_never_becomes_probabilistic` greps the tree for the usual libraries
and fails on a hit.

### The N:1 join, and what happens when it does not tie out

150 payout groups across the corpus, 147 tying out within ₹1.00. The widest aggregates 72
settlement rows. When a group does not tie out, reporting "off by ₹1,698.04" is not useful
to a bookkeeper — so the matcher runs a **bounded** residual search (a greedy pass, then
short combinations, both capped by `subset_search` in `thresholds.yaml`) and names the
rows the credit does not account for. When the bounded search finds nothing it says so:
shortfall, full candidate list, `search_exhausted: true`. It never widens the search until
something fits. An invented explanation is worse than an unresolved one.

### Late is not lost

A settlement arriving inside `date_window_days` of its order is a settlement cycle, not
missing money, and is never flagged. Past the window it is flagged — as a `variance` with
an impact of ₹0.00, because the money is right and only the timing is wrong. An order that
has not settled at all and is still inside its window is carried forward, not queued: it
is not a human's problem until the window has actually elapsed.

### Tolerances come from the ledger, not from a constant

The value-level band is a percentage of the order's own `expected_fee` — which is
`order_value * expected_commission_rate` — with the rounding tolerance as a floor. A stale
rate therefore produces a variance *proportional to the order*, which is what makes fifty
stale-rate exceptions look like one rule rather than fifty coincidences.

### Purity, and where the I/O lives

Nothing under `pipeline/matcher/` opens a file, reads a clock or draws a random number;
`test_the_matcher_package_performs_no_io` greps for all three. CSV reading is in
`pipeline/loader.py`, cross-batch state is in `pipeline/run.py`, and the matcher is handed
a self-contained universe. That is what makes "deterministic by choice" checkable rather
than asserted.

Nothing goes through pandas either. A pandas column of `Decimal` is an object column with
float coercion one careless operation away, and the money-is-never-a-float rule is worth
more than the ergonomics.

### Quarantine, never drop

Rows are validated one at a time, so one malformed row cannot take its table with it. A
row the models refuse is parked with a named reason (`malformed_unparseable_date`,
`malformed_missing_order_id`, …), the raw text kept for a human, and counted. The five
corruptions planted in checkpoint 1 all land there.


---

## The harness

`make score` runs the pipeline across all ten batches, scores it against
`data/truth`, prints a report and writes `EXCEPTIONS.md` and `data/score.json`.

It was built **before** the learning loop, not after, and that is the whole point.
Built afterwards a harness becomes a thing that confirms what you hoped. Built first
it is a thing that catches what went wrong — four of the six defects in FAILURES.md
#7–#14 were found by pointing it at code that passed all of its own tests.

### What it reports

| | |
|---|---|
| Auto-match rate | 81.4% in batch 1, 58.0% by batch 10 |
| Review rate | percentage of **settlement rows**, so a growing batch does not flatter it |
| Cause-level confusion | which bucket each of the 371 injected rows actually landed in |
| Silent clears | 48 rows, with the tightest headroom that let each one through |
| Throughput | records/second per batch, plus token cost per reconciled transaction |
| Honesty | 666 open exceptions itemised in `EXCEPTIONS.md`, 5 rows quarantined with reasons |
| Matcher precision | 658 of 666 findings trace to an injection; the other 8 are the quarantined rows and the bank side of the duplicates |

The declining match rate is the corpus behaving as designed, not the matcher
degrading: batch 10 receives every cross-batch reversal and refund lag planted in
batches 8 and 9 and emits none of its own.

### Silent clears, and why the count alone is useless

The checkpoint calls this "the number that tells you your tolerance band is wrong". A
count cannot tell you that — every silently cleared row is inside a tolerance by
definition, since that is what `matched` means. So the harness reports the **tightest
headroom** beside it: the smallest gap between a cleared row's deviation and the band
that permitted it.

`rounding_variance` clears with as little as **₹0.16 of headroom under the ₹1.00
floor** — one injected paise drift came within sixteen paise of firing.
`settlement_lag_crossing_batch` clears at ₹0.00 deviation with the band untouched.
Both are inside a band on purpose; only the first is a band worth arguing about. No
threshold was changed in either direction.

### New findings versus aged ones

An order that goes overdue in batch 5 and is never paid is correctly unmatched in
batches 5 through 10 — the ledger row is an input in each of them, and every input row
gets a verdict. That is right for the bucket contract and wrong for a review queue:
three missing settlements would otherwise appear as 39 queue entries.

`harness/aging.py` splits them. A finding is *new* the first time a row carries a
reason and *aged* after; the queue counts new findings, the report shows aged ones in
their own column, and `EXCEPTIONS.md` itemises each once in the batch it was raised.
The split lives in the harness because the matcher is a pure function of one batch and
cannot know it has seen a row before — the harness sees all ten and can.

### Determinism, and the one part that is not

Wall clock is a required metric and cannot be reproducible. Rather than drop it, it is
segregated: `data/score.json` carries a single `timings` block marked
`"reproducible": false`, and everything else in the file is byte-identical run to run.
Two tests assert it — one strips the block and compares the rest, the other asserts no
float appears anywhere outside it.

### What checkpoint 3 reads

The matcher emits the features a learned rule is written in, so induction and
application cannot each invent their own definition of the same band:

- `fee_variance_pct` / `net_variance_pct` — the percentage a commission rule is stated
  in. `None`, not `0.00%`, when the books expected nothing: a percentage of zero is
  undefined, and zero would read as "no variance" on the rows with the largest one.
- `days_after_settlement` / `days_since_order` on a deduction that arrives after its
  order closed — because in this corpus each lag cause was injected on a single
  channel, and a rule induced on the channel would score 100% having learned the
  injection plan rather than the phenomenon.
- `review_rate` and `net_review_rate` as separate series. The first is the matcher's
  own number and must not move; the second subtracts what rules resolved and is what
  the chart plots. One column cannot tell a decline earned by learning from one
  bought by widening a tolerance.

Three properties the learning loop depends on are held in place by tests, because each
is a dataset property nothing else would notice breaking: the stale-rate band is a
point per channel (8.80% Myntra, 7.50% Amazon), the held-out promo cause sits outside
it at 25–40%, and the near-miss sits exactly inside it on the same channel.

### LLM cost: zero, and tested at non-zero anyway

Nothing calls a model, so every cost the report prints is ₹0.00. The arithmetic is
still driven by tests with real token counts against the real rates in
`config/pricing.yaml`, because a cost path only ever exercised at zero would get its
first real run in the same commit that adds the model — and a wrong rate and a wrong
token count are indistinguishable at that point.

`pipeline/llm/usage.py` holds the ledger checkpoint 3's client records into.
`harness/score.py` already reads it, and already scores auto-resolution precision
against the answer key over an empty proposal list. Precision over zero attempts is
reported as **undefined**, not as 100%.

---

## Boundaries enforced in code

Two graded criteria that are easy to state in a README and easy to break in a repo, so
both are asserted in `tests/test_boundaries.py`:

- **`data/truth` is written by the generator and read only by `harness/`.** The test
  fails if anything under `pipeline/` so much as mentions the path — and a second test
  asserts `harness/truth.py` is the *only* module that reads it, so the first one
  cannot pass merely because nobody reads the key at all.
- **LLM calls may only live in `pipeline/llm/`.** The test greps the whole source tree
  for the `anthropic` import and fails on any hit outside that package. No LLM code
  exists yet; the test exists now, before it is first at risk.
- **Matching never becomes probabilistic.** The test greps for `recordlinkage`,
  `rapidfuzz`, `fuzzywuzzy`, `difflib` and friends anywhere in the source tree.

---

## The dataset's deliberate difficulties

These are in the data on purpose. Removing one makes the demo look better and the system
worse.

- **Held-out causes.** `promo_cofunding_deduction` does not appear before batch 7,
  `chargeback_deduction` not before batch 9 — verified against both the answer key and
  the raw CSVs. The system must correctly *fail* to auto-resolve these on first sight.
  Correct abstention is the hardest behaviour to fake.
- **Two near-misses** (batches 5 and 8). Myntra rows whose surface signature is
  identical to the learnable stale-rate rule — same channel, same ledger rate, same
  charged rate, same variance band — but whose true cause is
  `short_payment_unexplained`, routed to the claims queue rather than the learning loop.
  A rule induced from the stale-rate exceptions will fire on these and be wrong. That is
  the false positive to feature, not to remove.
- **One-off causes in every batch**, including the last two. This is why the review rate
  plateaus above zero. A curve to zero reads as scripted, because it is.
- **Batch 1 is not flattered.** Its trouble rate is genuinely high; that is the starting
  point the decline is measured from.
- **Both refund sign conventions.** Amazon, Myntra and the website negate the amount;
  Flipkart and the POS report a positive amount against the debit column. Same money,
  and the matcher has to normalise it.
- **Three claim-recovery pairs** planted 2-4 batches after the claim, so checkpoint 4's
  auto-close has something real to close against.
- **Paise drift** inside the rounding tolerance, which should cost nobody any attention.

---

## Reproducibility

`make generate` is seeded from `config/generation.yaml` and produces byte-identical
output on every run. `tests/test_determinism.py` asserts it three ways: two fresh runs
match each other, a fresh run matches the committed `data/generated`, and a different
seed produces a different world — so determinism comes from the seed rather than from the
generator ignoring it.
