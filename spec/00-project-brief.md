# Tallytrace — Project Brief (shared invariants)

Read this before any checkpoint file. It holds everything the four checkpoints share:
the data model, the frozen enums, the thresholds, the AI boundary policy, and the code
quality bar. Checkpoint files reference this document rather than restating it.

---

## What is being built

A reconciliation agent for a multi-channel Indian apparel seller (Amazon, Flipkart,
Myntra, own website, offline POS). It reconciles three sources, surfaces what it
cannot match, learns from how a human resolves those exceptions, and applies what it
learned to later batches so the manual queue shrinks.

**One loop.** Everything below is that loop plus two thin surfaces on top of the same
reconciled data. Do not build parallel pipelines.

### The three user-facing pillars

1. **Learning from fixes** — human resolves an exception in plain language; the system
   induces a generalised rule; later batches get a batch proposal ("this correction
   fixes N rows worth ₹X — accept / review") instead of N repeated exceptions.
2. **Claims queue** — exceptions where an external party owes money become tracked
   claims with a per-platform deadline clock and a drafted message; they auto-close when
   the recovery credit appears in a later batch.
3. **Ask and pin** — plain-language questions map to a fixed metric registry; confirmed
   results can be pinned to a dashboard, after which they recompute deterministically.

---

## Data model

### `settlement_report` (from platform / gateway)
One row per transaction.

| field | type | notes |
|---|---|---|
| `entity_id` | str | unique row id |
| `type` | enum | `payment` \| `refund` \| `transfer` \| `adjustment` |
| `channel` | enum | `amazon` \| `flipkart` \| `myntra` \| `website` \| `offline` |
| `order_id` | str | nullable for `adjustment` rows |
| `amount` | decimal | gross, signed |
| `fee` | decimal | platform + fulfilment fee |
| `tax` | decimal | GST on fee |
| `tcs` | decimal | 1% collected at source |
| `tds` | decimal | 0.1% under 194-O |
| `debit` | decimal | |
| `credit` | decimal | |
| `settlement_id` | str | groups rows into one payout |
| `settlement_utr` | str | bank reference for the payout |
| `created_at` | date | transaction date |
| `settled_at` | date | payout date; may fall in a later batch |
| `on_hold` | bool | held, not lost (e.g. weight dispute) |
| `dispute_id` | str | nullable |
| `description` | str | free text as platforms actually emit it |

### `bank_statement`
One row per credit landing in the single bank account.

| field | type |
|---|---|
| `utr` | str |
| `amount` | decimal |
| `created_at` | date |
| `status` | enum (`processed` \| `reversed`) |

### `internal_ledger` (the seller's own books)

| field | type | notes |
|---|---|---|
| `order_id` | str | |
| `channel` | enum | |
| `order_value` | decimal | |
| `expected_commission_rate` | decimal | **config, can go stale — this is deliberate** |
| `expected_fee` | decimal | derived from rate |
| `expected_net` | decimal | |
| `status` | enum | `booked` \| `matched` \| `exception` \| `resolved` \| `written_off` \| `claimed` |
| `resolution_reason` | str | nullable, free text written by the human |

### Joins
- **Order level** — `settlement_report.order_id` ↔ `internal_ledger.order_id`
- **Bank level** — `settlement_report.settlement_utr` ↔ `bank_statement.utr`, **N:1**
  (many settlement rows sum to one bank credit)
- **Value level** — `settlement_report.fee`/`amount` vs `internal_ledger.expected_fee`/
  `expected_net` for the same order. Variance detection fires here.

---

## Frozen enum: exception causes

Nothing anywhere in the system may invent a cause outside this list. The LLM is
constrained to it. The dataset generator draws from it. The harness scores against it.

| cause | resolution class | description |
|---|---|---|
| `commission_rate_stale` | internal_fix | ledger's expected rate is out of date |
| `commission_slab_change` | internal_fix | platform moved the item to a different slab |
| `fee_mismatch_other` | internal_fix | shipping / fulfilment / payment fee differs |
| `rto_reversal_later_cycle` | internal_fix | RTO deduction lands in a later settlement than the sale |
| `refund_timing_lag` | internal_fix | refund deducted in a different batch than booked |
| `settlement_lag_crossing_batch` | internal_fix | sale in batch N settles in batch N+k |
| `rounding_variance` | internal_fix | paise-level |
| `duplicate_settlement_row` | internal_fix | same transaction emitted twice |
| `tcs_timing_mismatch` | tax_review | TCS timing differs from expectation |
| `tds_timing_mismatch` | tax_review | TDS timing differs from expectation |
| `weight_dispute_hold` | counterparty_claim | payment held pending dispute, not lost |
| `missing_settlement_row` | counterparty_claim | order in ledger, absent from settlement |
| `short_payment_unexplained` | counterparty_claim | net short with no identifiable cause |
| `chargeback_deduction` | counterparty_claim | dispute deduction |
| `promo_cofunding_deduction` | counterparty_claim | platform promo cost shared without notice |
| `bank_credit_unmatched` | investigate | credit with no settlement counterpart |

`resolution_class` drives routing: `internal_fix` → learning loop, `counterparty_claim`
→ claims queue, `tax_review` → always human, `investigate` → always human.

---

## Thresholds (`config/thresholds.yaml` — never hardcode these inline)

```yaml
matching:
  rounding_tolerance_inr: 1.00
  date_window_days: 21
  fee_variance_tolerance_pct: 0.5

auto_resolution:
  max_variance_inr: 500.00          # the default: above this, never auto-resolve
  max_variance_overrides: []        # per-cause / per-channel ceilings the business sets
  never_auto_resolve_causes:        # regardless of rule confidence
    - tcs_timing_mismatch
    - tds_timing_mismatch
    - chargeback_deduction

rule_lifecycle:
  promotion_min_confirmations: 3
  promotion_min_precision: 0.90
  retirement_precision_floor: 0.75
  retirement_min_observations: 5

claims:
  deadline_days:
    amazon: 30                       # SAFE-T window
    flipkart: 30
    myntra: 30
    tcs_discrepancy: 10              # day-of-month cutoff, not a duration
```

---

## AI boundary policy

This is a graded criterion. It must be true in the code, not just in the README.

**LLM is used in exactly three places, all of them natural-language boundaries:**

1. Hypothesis generation — explaining why a row failed to match (constrained to the enum)
2. Rule induction — turning a human's free-text resolution reason into a structured rule
3. Intent mapping — mapping a plain-language question to a registered metric

**LLM is deliberately NOT used for:**

- Matching. Money does not want probabilistic matches. Exact keys plus explicit
  tolerance bands only. No fuzzy or probabilistic linkage.
- Applying learned rules. Induction is language work; application is a deterministic
  predicate evaluation.
- Computing any metric. The registry computes; the LLM only selects.
- Generating SQL. Enterprise text-to-SQL execution accuracy runs ~21–39% on realistic
  schemas. A fixed metric registry sidesteps this entirely.
- Deciding whether something may be auto-resolved. Thresholds are code.

**Enforcement:** LLM calls may only appear in `pipeline/llm/`. Add a test that greps the
rest of the tree for the client import and fails if found.

---

## Repo layout

```
/config          thresholds.yaml, causes.yaml, channels.yaml
/data
  /generated     the 10 batches the pipeline reads
  /truth         ground-truth answer keys — PIPELINE MUST NEVER READ THIS
/generator       synthetic data generation
/pipeline
  /matcher       deterministic matching
  /llm           the only place LLM calls may live
  /rules         induction, lifecycle, application
  /claims        claim objects, deadlines, recovery matching
  /metrics       the metric registry
/harness         scoring script
/ui              plugs into the existing dummy UI
/tests
FAILURES.md      running log — start it now, append as you go
EXCEPTIONS.md    generated: real unresolved list from a real run
README.md
Makefile
```

---

## Code quality bar

Most of this will be written by coding agents. Agents produce plausible code that
quietly does the wrong thing, so these are enforcement rules, not style preferences.

- **Decimal, never float.** All money is `decimal.Decimal`. A float in a money path is a
  bug. Add a test that asserts types on the matcher's inputs.
- **Config, not constants.** Any number that could be argued about lives in
  `config/`. If an agent inlines `0.22` or `500`, reject it.

  `max_variance_inr` is the **default** ceiling, not the only one: one number for every
  case is a policy about the average case and there is no average case. The business
  scopes ceilings under it by cause and by channel through `max_variance_overrides`
  (most specific wins, an equally specific tie goes to the lower ceiling, the same
  scope twice is a load error, and `0.00` disables auto-resolution for a scope). This does not relax checkpoint 3's "do not
  tune the guardrails to raise the auto-resolution rate" — that instruction is about
  who the number belongs to. Moving it is a business decision that has to be visible,
  so the governing ceiling and who set it travel in every decision's guardrail detail,
  the score report opens with the policy in force, and `--max-variance-inr` is a
  what-if that writes no artifact. Tuning the shipped default to flatter a metric is
  still the thing not to do.
- **Pure functions in the matcher.** Matching functions take data and config, return
  results. No I/O, no global state, no side effects. This is what makes them testable
  and what makes the "deterministic by choice" claim verifiable.
- **No silent excepts.** `except Exception: pass` is never acceptable. Malformed input
  is quarantined and counted, never swallowed.
- **Type hints on every public function.** Run `mypy` in CI.
- **Every module gets tests before the next checkpoint starts.** A checkpoint is not
  done if its tests do not exist.
- **No new dependencies** beyond: pandas, pydantic, pyyaml, pytest, mypy, anthropic,
  streamlit (or your existing UI stack). An agent adding a package needs a reason.
- **Functions under ~50 lines.** Agents write sprawling functions; split them.
- **Determinism.** Seeded generation, `temperature=0`, structured outputs, LLM responses
  cached to disk as fixtures. `make demo` must produce identical numbers twice.

### Working with coding agents

- Give the agent one checkpoint file, not all four.
- After each agent session, read the diff yourself. Do not merge unread code — the
  build-quality criterion is judged on code you will be asked about.
- When an agent's tests pass but you do not understand the code, that is a failure, not
  a success. Ask it to simplify.

---

## Ground-truth discipline

`/data/truth` is written by the generator and read only by `/harness`. The pipeline must
never import from it. Add a test that fails if `pipeline/` references the truth path.

The ground truth records, per injected trouble: the affected row ids, the true `cause`,
the true rupee impact, and the correct `resolution_class`. It does **not** record whether
the trouble "should" be matcher-solvable — that is the matcher's job to reveal, not the
dataset's job to assert.
