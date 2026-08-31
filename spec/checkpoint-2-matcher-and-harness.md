# Checkpoint 2 — Deterministic Matcher + Measurement Harness

**Read `00-project-brief.md` first. Checkpoint 1 must be fully done.**

Two things in one sitting, and the pairing is deliberate. The harness exists before the
learning loop so that when the learning loop arrives you already have an instrument
telling you whether it works. Built afterwards, it becomes a thing that confirms what you
hoped rather than a thing that catches what went wrong.

No LLM code is written in this checkpoint. None.

---

## Part A — The matcher

### 1. Ingestion and normalisation

- Load the three tables for a given batch into the pydantic models.
- Normalise sign conventions across channels so refunds and fees are consistently
  negative.
- **Quarantine, never drop.** Malformed rows go to a rejection report with the reason.
  Count them. A reconciliation tool that loses rows is worse than useless, and the
  rejection file is a five-second trust win in the video.

### 2. Order-level matching

`settlement_report.order_id` ↔ `internal_ledger.order_id`. Exact key. Report orders
present in one side and absent from the other.

### 3. Bank-level matching — N:1

This is the technically substantial part of the whole build. Give it the time.

One bank credit corresponds to many settlement rows: payments minus refunds minus fees.
Group settlement rows by `settlement_utr`, sum, and compare to the bank credit within
`rounding_tolerance_inr`.

When it does not tie out, do the harder thing: **identify which subset explains the
credit and what is missing.** Given the credit amount and the candidate pool, find the
subset that sums to it and report the residual rows. Keep this bounded — a greedy pass
plus a small exhaustive search over the residual is enough; do not build a general
subset-sum solver. Report the shortfall amount and the candidate rows either way.

### 4. Value-level variance detection

For matched orders, compare `settlement_report.fee`/net against
`internal_ledger.expected_fee`/`expected_net`. Fire a variance when outside
`fee_variance_tolerance_pct`.

Tolerance bands derive from the ledger's own `expected_commission_rate` config, not from
hardcoded constants. This matters: it is what makes a stale rate produce a *systematic*
variance the learning loop can generalise from, rather than noise.

### 5. Date-window handling

A settlement falling `date_window_days` after its order date is normal, not missing.
This is what stops cross-batch settlement lag from being flagged as lost money.

### 6. Output buckets

Every input row lands in exactly one bucket, with a reason code:

- `matched` — clean
- `variance` — matched but numerically off
- `unmatched` — missing counterpart on one side
- `quarantined` — malformed input

The reason code is a machine-readable string explaining which check produced the
verdict. Checkpoint 3 consumes it. The UI shows it.

### Implementation constraints

- Pure functions: data + config in, results out. No I/O inside matching logic.
- Plain Python and pandas. **No `recordlinkage`, no fuzzy matching, no probabilistic
  linkage.** In finance, a 0.87-confidence match is not a match, it is a liability. This
  is a deliberate design choice and it goes in the README.
- `Decimal` throughout.

---

## Part B — The harness

One command. `make score`. Runs the pipeline across all ten batches and prints a report.

### Metrics

**Throughput**
- Records processed, total and per batch
- Wall clock per batch, records/second
- LLM tokens per batch and rupee cost per reconciled transaction (zero for now; the
  plumbing must exist so checkpoint 3 fills it in without rework)

**Accuracy — against `/data/truth`**
- Auto-match rate per batch
- Manual review rate per batch, as a **percentage of batch total**
- Auto-resolution precision (zero for now — plumbing again)
- **Cause-level confusion:** for each injected trouble, which bucket did the matcher put
  it in? This is the number that tells you your tolerance band is wrong.
- **Silent-clear count:** injected troubles the matcher marked `matched`. This should be
  near zero. If it is not, your tolerance is too wide and everything downstream inherits
  the error.

**Honesty**
- Itemised unresolved exception list, written to `EXCEPTIONS.md`
- Quarantined row count and reasons

### Constraints

- Reads `/data/truth`; the pipeline does not.
- Output is a plain text report to stdout plus a JSON artifact the UI and the chart
  can consume.
- Deterministic. Same input, same numbers, every run.

---

## The checkpoint gate

**Stop here and look at the output before writing any more code.**

Run the matcher on batch 1 alone. Then answer:

1. Is the match rate plausible? Somewhere in the 60–85% range is expected for batch 1.
   If it is 99%, your tolerance is too wide and you are silently clearing real troubles.
   If it is 30%, something is broken in normalisation.
2. Does the silent-clear count sit near zero?
3. Read twenty flagged exceptions by hand. Do they look like real problems a bookkeeper
   would recognise, or do they look like arithmetic artifacts?
4. Does the N:1 grouping actually tie out for the clean settlements?

If any answer is wrong, fix the matcher now. Everything in checkpoint 3 is built on top
of these buckets, and a learning loop trained on a broken matcher will learn confident
nonsense.

---

## Done conditions

- [ ] `make score` runs across all ten batches from a clean clone.
- [ ] Every row lands in exactly one bucket with a reason code.
- [ ] N:1 aggregation ties out for clean settlements; shortfall and candidate rows
      reported when it does not.
- [ ] Quarantine path exercised by the malformed rows from checkpoint 1.
- [ ] Silent-clear count near zero, and you know what "near zero" means numerically.
- [ ] Cause-level confusion table prints.
- [ ] `EXCEPTIONS.md` generated from a real run.
- [ ] Zero LLM imports outside `pipeline/llm/` — enforced by a test.
- [ ] Matcher functions are pure and unit-tested against hand-built fixtures.
- [ ] The four gate questions above answered, in writing, in `FAILURES.md`.

---

## Do not

- Do not widen tolerances to make the match rate look better. The match rate is a
  measurement, not a target. A tuned tolerance that hides troubles is the exact failure
  the "no cherry-picked demos" bar is aimed at.
- Do not skip the gate because the code compiles.
- Do not defer the harness to later. It is half of this checkpoint for a reason.
