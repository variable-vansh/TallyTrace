# Tallytrace

A reconciliation agent for a multi-channel Indian apparel seller (Amazon, Flipkart,
Myntra, own website, offline POS). It reconciles three sources, surfaces what it cannot
match, learns from how a human resolves those exceptions, and applies what it learned to
later batches so the manual queue shrinks.

**Status: checkpoints 1, 2 and 3 complete** — the data foundation, the deterministic
matcher, the measurement harness, and the learning loop. The claims queue (checkpoint 4)
is not built yet.

Headline numbers from `make score` over the ten shipped batches:

| | |
|---|---|
| Auto-resolution precision | **98.63%** over 146 scored resolutions |
| Human decisions per batch | **22.03% → 6.08%** of the batch, plateauing above zero |
| Rules learned / active / retired | 31 / 9 / 1 |
| Correct abstention on held-out causes | **100%**, both, on first sight and ever |
| Model spend | ₹141.51 total, **₹0.117 per settlement row** |

The two rows separating 98.63% from 100% are the two deliberate near-misses planted in
checkpoint 1. Nothing hides them. `FAILURES.md` #21 explains the one done condition this
checkpoint does not fully meet and the three shortcuts that would have met it.

---

## Quick start

```bash
make venv           # .venv + pinned dependencies
make generate       # ten batches + ground truth, seeded and reproducible
make reconcile      # run the matcher across all ten batches
make learn          # the learning loop: hypotheses, rules, guardrails, provenance
make score          # score it against the answer key; writes EXCEPTIONS.md
make ui-data        # build the JSON the React UI reads
make demo           # all of the above. Run it twice: the numbers do not move.
make check          # clean-base verification, tests, mypy
```

```bash
cd ui && npm install && npm run dev     # the dashboard, queue, rules and reports
```

No API key is needed. Every model response is cached in `data/llm_cache/` and the
pipeline runs from disk; see "The LLM boundary" below for what that cache is and how to
repopulate it over the wire.

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
config/     thresholds.yaml, causes.yaml, channels.yaml, generation.yaml, pricing.yaml
data/
  generated/        batch_01 .. batch_10, three CSVs each
  truth/            the answer key — PIPELINE MUST NEVER READ THIS
  llm_cache/        cached model responses, committed: this is what makes a rerun free
  resolutions.json  the operator's own words; the root of every provenance chain
generator/  synthetic world, injectors, writer, clean-base verifier
pipeline/   models.py, config.py, loader.py, run.py, cases.py, learn.py
  matcher/    deterministic matching
  llm/        the only place a model may be called
  rules/      induction targets, lifecycle, predicates, guardrails, provenance
harness/    scoring: reads data/truth, which the pipeline never does
tools/      fixture and artifact builders; not imported by the pipeline
ui/         React dashboard, fed by one scored run
tests/
```

---

## Design decisions worth knowing

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

### The features the learning loop reads

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
  own number and must not move; the second subtracts what rules resolved. One column
  cannot tell a decline earned by learning from one bought by widening a tolerance.

Three properties the learning loop depends on are held in place by tests, because each
is a dataset property nothing else would notice breaking: the stale-rate band is a
point per channel (8.80% Myntra, 7.50% Amazon), the held-out promo cause sits outside
it at 25–40%, and the near-miss sits exactly inside it on the same channel.

---

## The learning loop

Seven steps per batch, in this order, and the order is the product.

1. **Reconcile.** The matcher runs exactly as it did in checkpoint 2. Nothing
   downstream can change a bucket.
2. **Build the queue.** Verdicts are grouped into **cases** — a wrong commission rate
   produces a verdict on the settlement row and one on the ledger row, and a bookkeeper
   works that once. New findings only; an order overdue for four weeks is one problem.
3. **Hypothesise.** Every readable case gets a cause and a plain-English explanation
   from the model, constrained to the frozen enum by the schema.
4. **Decide.** The rule store is consulted. Active rules may auto-resolve, subject to
   the guardrails; shadow rules predict and log; an unmatched case goes to a human
   untouched.
5. **Card decisions.** What the operator did with last week's proposals is applied.
6. **Resolve.** The operator's free text for this batch is read, shadow predictions on
   those cases are judged against what the human actually said, and new rules are
   induced from any resolution that does not corroborate a rule already held.
7. **Advance.** Every rule's lifecycle state is recomputed from its record.

Step 4 before step 6 matters: a rule must predict *before* it is told the answer, or its
precision measures nothing. Step 7 last, so a rule promoted this week starts firing next
week rather than retroactively.

### The lifecycle, and why the lag is the point

`proposed` → `shadow` → `active` → `retired`. A rule induced in batch 1 shadows batch 2
and fires from batch 3 at the earliest. Promotion needs both
`promotion_min_confirmations` **and** `promotion_min_precision`; volume alone is not
evidence, and a shadow prediction nobody has ruled on is not a confirmation.

Retirement is automatic and it is shown, not hidden. **R-07** was induced in batch 2
from a note that generalised across every marketplace, predicted on six late deductions
in batch 3, was contradicted by the operator's own Amazon resolutions, and retired
itself at 40.00% precision over five judged observations. The rules page shows it in red
with the reason. `FAILURES.md` #24 has the full story, including why the note that
caused it was not quietly rewritten.

### Guardrails run after the rule matches, and they override it

That ordering is the whole design. A rule's confidence is an opinion about a pattern; a
threshold is a decision about risk, and the opinion never wins. All three are evaluated
every time, pass or fail — a short circuit would lose the record, and "which guardrails
did you check?" is a question asked about the resolutions that went *through*.

| guardrail | source | effect |
|---|---|---|
| `max_variance_inr` | `config/thresholds.yaml` | above ₹500, never auto-resolve |
| `never_auto_resolve_causes` | same | TCS, TDS and chargebacks, whatever a rule believes |
| resolution class | `config/causes.yaml` | `tax_review`, `investigate` and `counterparty_claim` are always human |

The third includes claims deliberately: closing a row someone else owes money on is not
a resolution, it is a write-off nobody authorised.

The visible consequence is that the system **automates volume and escalates value** —
₹8,203 auto-resolved against ₹490,401 escalated across the corpus. That is the
guardrails working, not a limitation, and the report prints both figures beside each
other so the ratio cannot be quietly inverted.

### Two review series, because one would flatter

| batch | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| matcher alone | 18.6 | 22.7 | 25.3 | 32.4 | 29.8 | 27.3 | 29.8 | 31.0 | 32.7 | 42.0 |
| rows a human still owns | 18.6 | 22.7 | 18.4 | 25.5 | 21.9 | 12.5 | 14.9 | 15.5 | 17.9 | 22.7 |
| decisions a human makes | 22.0 | 22.7 | 16.1 | 25.5 | 25.4 | 14.1 | 10.6 | 9.7 | 9.5 | **6.1** |

The middle row is the strict reading and it ends above where it started. The bottom row
counts *distinct decisions*: an unmatched case counts once, and a batch proposal card
counts once no matter how many rows it collapses. Batch 10 leaves 41 rows with a human
and asks them 11 questions.

Both are true and they measure different things. `FAILURES.md` #21 sets out exactly why
the middle row does not decline, and the three ways of making it decline that I refused.

### Provenance

Every auto-resolution records the rule, its state when it fired, the resolution it
descends from, the operator who wrote that resolution, the proposed cause, and every
guardrail evaluation. The UI renders the record verbatim — click any exception or any
flagged transaction row and you get the whole path: what the matcher measured, what the
model guessed and at what confidence, which rule matched and at what specificity, which
guardrails ran, and the sentence a person typed that the rule came from.

### Live precision versus true precision

A rule's **live precision** comes from what the operator's later resolutions said. Its
**true precision** comes from the answer key, which the pipeline never sees. Both are
printed, adjacent, because an operator and a rule can be wrong in the same direction:
R-05 fires on the near-miss rows, the operator confirmed them too, and its live
precision reads 100.00% against a true precision of 97.44%. That gap is the two planted
near-misses and it is the entire difference between 98.63% and a suspiciously clean 100%.

---

## The LLM boundary

The model is used in exactly two places in this build, both natural-language
boundaries, both in `pipeline/llm/`:

1. **Hypothesis generation** — why did this row fail to match? Constrained to the frozen
   enum *in the JSON schema*, not merely in the prompt text. A cause outside the enum
   fails validation and raises; there is no fallback branch, because a fallback is how
   an invented cause reaches a bookkeeper wearing a confidence score.
2. **Rule induction** — the operator's sentence read into a schema. The schema has no
   field for an identifier, and `assert_generalisable` re-checks the free-text values,
   because `plain_words` will hold an order id quite happily if nobody looks.

It is deliberately **not** used for matching, for applying rules, for computing any
metric, for generating SQL, or for deciding whether something may be auto-resolved.
`pipeline/rules/` — where rule matching, precedence, guardrails and lifecycle live — is
pure predicate evaluation and cannot reach a client at all; a test asserts that
separately from the repo-wide one.

### Deduplication by question, not by row

The corpus raises 395 readable exceptions and asks **45 distinct questions**. Eighty-nine
of those cases are "Myntra, fee variance, short, 8.8% over" with nothing between them
but the paise. The prompt is built from a normalised signature, so identical questions
collapse to one cached answer by construction. Asking the same question eighty-nine
times would produce eighty-nine identical answers and a cost report overstating the
model by two orders of magnitude — and a hypothesis that *differed* between two
numerically identical rows would be non-determinism, not insight.

### The cache, and what its `source` field means

Responses are cached to `data/llm_cache/`, keyed by a hash of the model, the system
prompt, the user prompt and the output schema. Change any of them and it is a different
question, so it misses and is asked again. A cache hit is billed at the **cache-read**
rate rather than as free: the first run paid for the answer, and a per-transaction cost
that only counts cold runs is not a cost.

Every entry records where it came from. The entries shipped here carry
`source: "transcript"` — they were produced by Claude Opus reading each rendered prompt
through a coding session rather than over the HTTP API, because the machine this was
built on had no API key. The request text and the schema are byte-identical to what the
client sends; only the transport differs. The consequence is that their token counts are
*estimated* from character length rather than metered, and `make score` prints
**TOKEN COUNTS ARE ESTIMATED** in full whenever that is true. Recording zero instead
would report a model-backed pipeline as free, which is a more misleading number than an
approximate one.

To replace them with metered ones:

```bash
export ANTHROPIC_API_KEY=...
rm -rf data/llm_cache && make llm-fixtures && make demo
```

Nothing else changes. With no cache and no key, the client raises `CacheMiss` rather
than degrading silently.

---

## The operator, and why the resolution notes are messy

For this build I am the human. `data/resolutions.json` holds 195 resolutions written the
way a bookkeeper writes them — *"Myntra is billing 27.2% on these but our master rate
sheet still says 25%. Their category manager flagged in the January mailer that
outerwear moved up a slab."* — not in the shape the rule engine wants. Text engineered
to induce cleanly would have tested nothing, and one deliberately loose note is what
produces the retired rule.

The operator policy is the realistic one and it is stated in `tools/write_resolutions.py`:
work the whole queue for the first three batches, then work anything whose *shape* is
new and spot-check two of each familiar shape. The spot checks are what keep a promoted
rule's live precision a live number; without them a rule promoted in batch 3 could never
be judged again and could not retire, which would make the lifecycle decorative.

---

## Boundaries enforced in code

Graded criteria that are easy to state in a README and easy to break in a repo, so each
is asserted in `tests/test_boundaries.py`, `tests/test_llm.py` or `tests/test_rules.py`:

- **`data/truth` is written by the generator and read only by `harness/`.** The test
  fails if anything under `pipeline/` so much as mentions the path — and a second test
  asserts `harness/truth.py` is the *only* module that reads it, so the first one
  cannot pass merely because nobody reads the key at all.
- **LLM calls may only live in `pipeline/llm/`.** The test greps the whole source tree
  for the `anthropic` import and fails on any hit outside that package. A second,
  narrower test asserts `pipeline/rules/` imports neither `anthropic` nor the client
  module — rule application is arithmetic, and the package that does it must not be one
  edit away from asking a model instead.
- **No rule may contain a transaction id.** Enforced twice: the induced-rule schema has
  no field for one, and `assert_generalisable` checks the free-text values. A fourth
  test runs it over the shipped `data/rules.json`.
- **Matching never becomes probabilistic.** The test greps for `recordlinkage`,
  `rapidfuzz`, `fuzzywuzzy`, `difflib` and friends anywhere in the source tree.
- **One writer and one reader of the answer key.** `generator/main.py` is the only
  module that may name the truth path on the way in, `harness/truth.py` the only one on
  the way out. Two lines answer "who could have touched the answers?".

`tools/` is inside the greps as well as the packages: the fixture writer builds the same
prompts the client sends, and it is the obvious place for a shortcut that calls a model
directly and never goes through the cache.

One thing does deliberately cross a boundary: each exception in the UI file carries a
`trueCause`, so a scored run can point at its own false positives. The two planted
near-misses are the most useful rows in the demo and hiding them would defeat the
purpose. It arrives through the harness, and the UI labels it as coming from the answer
key everywhere it appears.

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

## The UI

`cd ui && npm install && npm run dev`. Everything on screen comes from one scored run
(`make score && make ui-data` writes `ui/public/tallytrace.json`), so the dashboard, the
queue and the rules page cannot disagree with each other or with the number the harness
printed in the terminal.

- **Dashboard** — all three review series on one chart, precision on its own beside it,
  and the auto-resolved-versus-escalated rupee split.
- **Review queue** — batch proposal cards first (one card instead of N exceptions,
  including the ones a guardrail held), then the exceptions themselves with the model's
  hypothesis, the operator's own words where they exist, and a decision path on every card.
- **Transactions** — the settlement report, the bank statement and the ledger, each with
  the verdict and reason code the matcher gave it. Clicking any flagged row opens its
  decision path.
- **Rules** — every rule with its state, conditions, support, live *and* true precision,
  full lifecycle history with reasons, and the resolution it descends from. The retired
  rule is at the top in red.
- **Reports** — money by week and channel, cause mix, the abstention result, and the
  quarantine list.

Money crosses one boundary to get here: `tools/build_ui_data.py::money` is the only
function in the repo that turns a `Decimal` into a float, because JavaScript has no
Decimal and the charts do arithmetic. `data/score.json` — the artifact anyone would
audit — keeps every amount as a string.

**The UI renders a completed run, and says so.** There is no server behind it, so
"Accept all", "Not this time", "Narrow the band" and "Disable" state what they *would*
record rather than writing back to `data/resolutions.json`. The queue carries that
sentence at the top rather than leaving a viewer to discover it. The paths behind those
controls are real code and are driven by tests — `_apply_card_decisions` under accept,
decline and defer, and `Rule.narrowed` including its refusal to widen a band.

---

## Reproducibility

`make generate` is seeded from `config/generation.yaml` and produces byte-identical
output on every run. `tests/test_determinism.py` asserts it three ways: two fresh runs
match each other, a fresh run matches the committed `data/generated`, and a different
seed produces a different world — so determinism comes from the seed rather than from the
generator ignoring it.

`make demo` runs the whole chain — generate, reconcile, hypothesise, learn, score, build
the UI data — and produces identical numbers twice. `data/score.json` is byte-identical
run to run apart from a single `timings` block that carries `"reproducible": false`,
because wall clock is a required metric and a deterministic artifact, and those cannot
both be true of the same numbers. Two tests hold that line: one strips the block and
compares the rest, the other asserts no float appears anywhere outside it.

The model is the other half of that claim. `temperature=0`, schema-constrained output,
and every response cached to disk keyed by a hash of the exact question — so a second run
asks nothing, costs nothing, and returns the same bytes.
