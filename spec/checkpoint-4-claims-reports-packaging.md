# Checkpoint 4 — Claims Queue, Reports, Packaging

**Read `00-project-brief.md` first. Checkpoints 1–3 must be fully done.**

The longest and most cuttable checkpoint. It contains one protected section.

**If time runs short, cut in this order:** reports (Part B) first, then the drafting
inside claims (keep the register and the deadline clock — they are the valuable part),
then batches 8–10. **Never cut Part C.** Packaging is not cleanup after the real work; the
README and the video are the only things a judge experiences.

Budget Part C at a minimum of a fifth of this sitting before you start Part A.

---

## Part A — Claims queue

The insight: the drafting is not what matters. The deadlines are. Amazon's SAFE-T window
is 30 days, and a TCS discrepancy on Flipkart must be raised before the 10th of the
following month to enable a GSTR-8 correction. Sellers miss these because they only
discover the loss at reconciliation time, which is already late.

### 1. Routing

Exceptions split by `resolution_class`:
- `internal_fix` → learning loop (checkpoint 3)
- `counterparty_claim` → here
- `tax_review` / `investigate` → human queue, no automation

### 2. Claim objects

```python
{
  "claim_id": "...",
  "platform": "amazon",
  "amount_inr": Decimal("4820.00"),
  "evidence_row_ids": [...],       # the actual settlement/ledger rows
  "cause": "missing_settlement_row",
  "opened_at": date,
  "deadline": date,                # from config/thresholds.yaml, per platform
  "status": "open" | "drafted" | "filed" | "recovered" | "expired" | "written_off",
  "draft": str | None,
  "recovery_row_id": str | None
}
```

### 3. Deadline clock

Computed from the claim's opening event and the per-platform config. TCS discrepancies
use a day-of-month cutoff, not a duration — handle that case separately rather than
forcing it into a days-remaining model.

### 4. Draft generation

One prompt, cheap. Order ids, invoice reference, expected vs received, a plain factual
statement of the discrepancy. No rhetoric — claims are decided on evidence quality, not
on explanation. Cached, `temperature=0`, in `pipeline/llm/`.

### 5. Auto-close on recovery

The part no email-drafting demo will have. When a later batch contains a credit matching
an open claim's amount and platform within tolerance, link it and move the claim to
`recovered`. The recovery is itself a reconciliation, so reuse the matcher.

Checkpoint 1 planted at least three recovery pairs. This is where they pay off.

### 6. The queue view

Sorted by **expiry, not creation date**. The header reads like:

> ₹47,300 open across 9 claims · 2 expiring in 4 days

### 7. Harness additions

- Claims opened / drafted / recovered / expired per batch
- Rupees recovered vs rupees expired
- Recovery match accuracy against ground truth

---

## Part B — Reports (cut first if short)

### 1. Metric registry

A fixed set of computable metrics over the reconciled data. Each has an id, a
plain-language description, parameters (date range, channel, grouping), and a pure
computation function. Examples: net revenue by channel, effective take rate by channel,
exception count by cause, review-rate trend, claim recovery rate, deductions as a share
of gross order value.

**No SQL generation.** Enterprise text-to-SQL execution accuracy runs roughly 21–39% on
realistic schemas. The registry sidesteps that entirely, and saying so explicitly in the
README converts this from a generic feature into an AI-judgment answer.

### 2. Intent mapping (LLM job 3)

Plain-language question → registered metric id plus parameters. Structured output,
constrained to registered ids.

- **One clarifying question** when the mapping is ambiguous, rather than guessing.
- **Confirm before compute:** state in words what is about to be computed, and wait.
- **Refusal path:** when nothing in the registry answers the question, say so. Do not
  produce a plausible adjacent chart.

### 3. Pin

A confirmed result can be pinned to the dashboard with a name. Pinned metrics recompute
deterministically every batch with **no LLM in the loop**. The model is present at the
moment of definition and absent from every run afterwards — say it that way when you
present it.

### 4. Fix the existing fees chart

Fees as a **percentage of gross order value**, not absolute rupees. Batch sizes grow, so
an absolute line says nothing. As a percentage, a rising line means the effective take
rate is climbing — which is exactly the signal that catches a silent commission change.

---

## Part C — Packaging (PROTECTED)

### 1. Reproducibility

- Seeded generation, LLM fixtures committed, `--offline` flag
- `make demo` runs end to end from a clean clone with no API key and produces identical
  numbers twice
- `mypy` and `pytest` clean

### 2. README

Structure:

- **First line:** the claim, with real numbers from a real run. *Reconciles N
  transactions across three sources at X% auto-match; learns from operator resolutions to
  cut manual review from A% to B% across ten batches at C% auto-resolution precision;
  escalates everything material.*
- **Problem** — the gap between dashboard revenue and bank balance, with the specific
  mechanics: commissions, TCS/TDS, RTO, settlement lag, weight-dispute holds.
- **What already exists** — name Unicommerce, EasyEcom, BlackLine, Numeric. Concede that
  deterministic matching is solved, and that learning from user corrections exists
  upmarket. **Do not claim novelty.** The claim is segment: this exists at enterprise
  cost and multi-month implementation, and it does not exist for an Indian marketplace
  seller doing a few crore across four channels.
- **Benchmark yourself** — put your auto-resolution rate next to BlackLine's published
  43–85% auto-certification range. Most submissions will not do this.
- **AI judgment** — the three places an LLM is used, and the five places it is
  deliberately not, each with its reason. Reference the enforcement test.
- **Results** — the harness output verbatim.
- **Limitations** — written by you before a judge writes them for you.

### 3. Honesty artifacts

- `EXCEPTIONS.md` — the real unresolved list from a real run
- `FAILURES.md` — the running log you have kept since checkpoint 1. Real specific bugs,
  not reconstructed generic ones. This is a graded criterion most entrants leave empty.

### 4. Architecture diagram

Organise it around the **AI boundary**. Shade the three LLM call sites, leave everything
else plain, label the plain regions "deterministic by choice." The diagram then answers
the AI-judgment criterion before anyone reads a word.

### 5. Five-minute video

1. The rupee gap, with a real number
2. What incumbents already solve, named — then the gap they leave
3. The loop running on one batch
4. The two-axis chart: review rate falling, precision holding
5. The system getting one wrong, and the guardrail catching it
6. One genuinely unresolved exception, on screen, unhidden

End on the failure. Every other video ends on a win, and the rubric explicitly grades
failure recovery.

---

## Done conditions

- [ ] `make demo` reproducible from clean clone, offline, identical numbers twice
- [ ] Claims auto-close against planted recovery credits
- [ ] Claims queue sorted by expiry with the summary header
- [ ] Pinned metrics recompute with no LLM call
- [ ] Registry refuses unmappable questions instead of guessing
- [ ] Every number in the README traced to a run
- [ ] `EXCEPTIONS.md` and `FAILURES.md` real and non-empty
- [ ] Architecture diagram organised on the AI boundary
- [ ] Video recorded, ending on the unresolved exception

---

## Do not

- Do not start Part A without having reserved time for Part C.
- Do not invent a number anywhere, including in the video voiceover.
- Do not build free-form Q&A because the registry feels limiting. The limit is the point,
  and it is the strongest thing you can say about this surface.
