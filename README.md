# Tallytrace

A reconciliation agent for a multi-channel Indian apparel seller (Amazon, Flipkart,
Myntra, own website, offline POS). It reconciles three sources, surfaces what it cannot
match, learns from how a human resolves those exceptions, and applies what it learned to
later batches so the manual queue shrinks.

**Status: checkpoint 1 (data foundation) complete.** No pipeline logic yet — this
checkpoint produces the config, the generator, ten batches of data, and the ground truth
everything after it is scored against.

---

## Quick start

```bash
make venv           # .venv + pinned dependencies
make generate       # ten batches + ground truth, seeded and reproducible
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
pipeline/   models.py and config.py so far: the contract everything codes against
harness/    scoring (checkpoint 2)
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

## Boundaries enforced in code

Two graded criteria that are easy to state in a README and easy to break in a repo, so
both are asserted in `tests/test_boundaries.py`:

- **`data/truth` is written by the generator and read only by `harness/`.** The test
  fails if anything under `pipeline/` so much as mentions the path.
- **LLM calls may only live in `pipeline/llm/`.** The test greps the whole source tree
  for the `anthropic` import and fails on any hit outside that package. No LLM code
  exists yet; the test exists now, before it is first at risk.

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
