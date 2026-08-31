# Checkpoint 1 — Data Foundation

**Read `00-project-brief.md` first.** This checkpoint produces no pipeline logic. It
produces the config, the generator, ten batches of data, and the ground truth the rest
of the build is scored against.

Everything after this depends on the data being right. A subtle generator bug becomes a
matcher bug becomes a learning-loop bug, and you will find it in checkpoint 3 with no
time left. Spend the sitting here.

---

## Goal

Ten weekly batches of the three tables in `00-project-brief.md`, built as **clean base +
injected troubles**, with a separate ground-truth key recording every injection.

---

## Tasks

### 1. Scaffolding

- Repo layout per the brief.
- `config/thresholds.yaml`, `config/causes.yaml` (the frozen enum with
  `resolution_class` per cause), `config/channels.yaml` (per-channel commission rates,
  settlement lag in days, RTO rate).
- `FAILURES.md` with a first entry. Append to it for the rest of the build.
- `Makefile` with a `generate` target.
- Pydantic models for the three tables. These are the contract every later module
  codes against.

### 2. Clean base generation

Generate a fully consistent world first — every order matches, every settlement ties to
a bank credit, every fee equals the ledger's expectation. This base must reconcile at
100% before you inject anything. **Verify that.** If the clean base does not reconcile
perfectly, the generator is wrong and every number after this is meaningless.

Per-channel realism:

| channel | commission | settlement lag | RTO rate | notes |
|---|---|---|---|---|
| amazon | 18–24% by category | 7–14 days | 25% | SAFE-T eligible |
| flipkart | 15–22% | 7–15 days by tier | 28% | weight disputes occur |
| myntra | 20–28% | 10–21 days | 30% | highest commission |
| website | 2% gateway fee | T+2 | 8% | Razorpay-shaped settlement |
| offline | 2% gateway fee | T+1 | 0% | POS |

TCS at 1% and TDS at 0.1% on marketplace channels. GST on commission.

**Batch sizes grow:** batch 1 ≈ 60 rows, batch 10 ≈ 180. Growth is deliberate — it is
what makes the percentage-based review-rate metric necessary rather than cosmetic. Every
batch must clear the contest's 50-record floor.

### 3. Trouble injectors

One injector function per cause in the frozen enum. Each takes the clean world and a
count, mutates rows, and appends to the ground truth. Injectors must be independent and
composable.

Each ground-truth entry records:

```python
{
  "batch": 4,
  "cause": "commission_rate_stale",
  "affected_row_ids": ["st_00412", "st_00418", ...],
  "affected_order_ids": [...],
  "true_impact_inr": Decimal("8340.00"),
  "resolution_class": "internal_fix",
  "injector_params": {"channel": "myntra", "stale_rate": 0.22, "actual_rate": 0.242}
}
```

It records **no** claim about whether a matcher should catch it. That is the harness's
finding, not the dataset's assertion.

### 4. Distribution across batches

This distribution is the whole learning story. Get it wrong and the curve is fake.

**Recurring categories** — appear in most batches, always with different order ids.
These are what the system should learn. Weight toward `commission_rate_stale`,
`rto_reversal_later_cycle`, `refund_timing_lag`, `settlement_lag_crossing_batch`.

**Held-out categories** — do not appear at all until late:
- `promo_cofunding_deduction` — first appearance batch 7
- `chargeback_deduction` — first appearance batch 9

The system must correctly *fail* to auto-resolve these on first sight. Correct
abstention is the hardest behaviour to fake and the easiest to demonstrate.

**Genuine one-offs** — 1–2 per batch, drawn from causes that do not repeat with a
consistent signature. These are why the review rate plateaus above zero instead of
hitting zero. A curve to zero reads as scripted.

**Near-miss** — at least two across the ten batches: a row whose surface signature
matches a learnable rule (right channel, variance in the right band) but whose true
cause in the ground truth is different. This is the false positive you will feature in
the video. Do not skip it because it feels like sabotage; it is the most valuable single
row in the dataset.

### 5. Cross-batch behaviour

Three things must span batches, and all three must exist now, not be retrofitted later:

- **Settlement lag** — a sale created in batch N settling in batch N+1 or N+2. The
  matcher must not flag this as missing money.
- **RTO reversal** — a sale in batch N with its return deduction landing in batch N+3.
- **Claim recovery** — for at least three injected `missing_settlement_row` or
  `short_payment_unexplained` troubles, plant the recovery credit in a later batch
  (batch N+2 to N+4). Checkpoint 4's auto-close depends on these existing. Build them
  now.

### 6. Realistic mess

Cheap to add, disproportionately credible:

- Description strings shaped the way platforms actually emit them, not clean labels.
- Sign convention inconsistency between channels (refunds positive on one, negative on
  another) — the matcher must normalise.
- 3–5 malformed rows across all batches: missing order id, unparseable date, amount as
  a string with a comma. These exercise the quarantine path in checkpoint 2.
- Paise-level rounding drift.

---

## Done conditions

Do not start checkpoint 2 until all of these hold:

- [ ] `make generate` runs from clean, seeded, and produces identical output twice.
- [ ] Clean base (injections disabled) reconciles at 100% — verify with a throwaway
      script; this is your generator's own unit test.
- [ ] Ten batches exist, sizes growing 60 → 180, all ≥ 50 rows.
- [ ] `/data/truth` exists and is not referenced anywhere in `/pipeline`.
- [ ] A test asserts the truth-path isolation.
- [ ] You can state, out loud, per batch: how many injected troubles, of which causes,
      worth how many rupees. If you cannot, the generator is not done.
- [ ] Held-out categories genuinely absent before batches 7 and 9 — verified by a test,
      not by eye.
- [ ] At least three claim-recovery pairs planted and recorded in ground truth.
- [ ] All money fields are `Decimal` in the generated files and the models.
- [ ] Tests exist for every injector.

---

## Do not

- Do not label troubles by expected difficulty. Cause only.
- Do not make batch 1 unrealistically clean to flatter the curve. Its review rate should
  be genuinely high — that is the starting point the decline is measured from.
- Do not generate all ten batches before checking batch 1 by hand. Read fifty rows of
  batch 1 yourself and confirm they look like a settlement report.
- Do not let an agent invent extra causes. The enum is frozen.
