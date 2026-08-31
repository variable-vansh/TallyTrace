# Checkpoint 3 — The Learning Loop

**Read `00-project-brief.md` first. Checkpoints 1 and 2 must be fully done, including
the gate.**

This is the differentiator and the longest sitting. It is also where the build most
easily becomes dishonest without anyone intending it, because a system that
auto-resolves aggressively produces a beautiful declining curve and is worthless.

The harness from checkpoint 2 is what keeps you honest. Watch precision, not just the
decline.

---

## 1. Hypothesis generation (LLM job 1)

For every `variance` and `unmatched` row, send the LLM the row, its counterparts across
all three tables (or the fact of their absence), and the matcher's reason code. Ask for:

- a cause, **constrained to the frozen enum** — enum values in the schema, not just in
  the prompt text
- a plain-English hypothesis a bookkeeper would recognise
- a confidence score

Constraints:
- `temperature=0`, structured output against a pydantic schema
- Responses cached to disk keyed by a hash of the input. Second run costs nothing and
  produces identical output.
- Lives in `pipeline/llm/`
- If the model returns a cause outside the enum, that is a hard error, not a fallback

Store the hypothesis on the row. The UI shows it in the queue.

---

## 2. Resolution capture

A human resolves an exception by typing a reason in normal language. No dropdowns, no
rule builder — the whole premise is that rules are induced from work someone was doing
anyway.

Store: the row, the free text, the timestamp, the operator. This record is the root of
every provenance chain in the system.

**For the build, you are the human.** Write a small script or UI step that walks the
batch-1 queue and lets you resolve items. Resolve them the way a bookkeeper actually
would — "Myntra is billing 24.2% but our master rate is 22%, they moved outerwear to a
new slab" — not in the shape you know the rule engine wants. If you write resolution
text engineered to induce cleanly, you have tested nothing.

---

## 3. Rule induction (LLM job 2)

Send the free text plus the row's features. Get back a structured rule:

```python
{
  "channel": "myntra",
  "cause": "commission_rate_stale",          # from the frozen enum
  "variance_band_pct": [-9.0, -7.0],
  "direction": "short",
  "lag_window_days": null,
  "resolution_class": "internal_fix",
  "action": {"type": "update_ledger_rate", "field": "expected_commission_rate", "value": 0.242}
}
```

Hard requirements:

- **Never an id lookup.** A rule containing an `order_id` or `entity_id` is a memorised
  transaction, not a learned pattern. Reject at validation. Add a test.
- Schema-constrained output, `temperature=0`, cached.
- The LLM's only job here is interpreting language into a schema. It does not decide
  whether the rule may fire.

---

## 4. Rule lifecycle

Four states. This is what makes the decline earned rather than instant, and it produces
the plateau naturally instead of you engineering one.

**`proposed`** — just induced. Never fires.

**`shadow`** — predicts on each new batch and logs whether it would have been right
(against ground truth in scoring; against the human's later resolution in the product
narrative). The human still sees the exception. Nothing auto-resolves.

**`active`** — promoted after `promotion_min_confirmations` observations at
`promotion_min_precision` or better. Now auto-resolves matching rows.

**`retired`** — demoted automatically when live precision drops below
`retirement_precision_floor` over at least `retirement_min_observations`. Retirement is
not a failure to hide; it is a feature to show.

Every transition is logged with a reason.

---

## 5. Rule application — deterministic

Rule matching is predicate evaluation. No LLM. Given a flagged row and the active rule
set: evaluate each rule's conditions, collect matches, apply precedence (most specific
first; ties go to human), then enforce the guardrails:

- variance magnitude above `max_variance_inr` → never auto-resolve, mark held with reason
- cause in `never_auto_resolve_causes` → never auto-resolve, mark held with reason
- resolution class `tax_review` or `investigate` → always human

The guardrails run *after* the rule matches and override it. A rule cannot out-confidence
a threshold. That ordering is the point.

---

## 6. Provenance

Every auto-resolution writes:

```python
{
  "row_id": "st_00412",
  "rule_id": "R-14",
  "rule_state_at_fire": "active",
  "source_resolution_id": "res_0031",   # the human resolution the rule came from
  "source_operator": "...",
  "fired_at": "...",
  "guardrails_evaluated": ["max_variance_inr:pass", "never_auto_resolve:pass"]
}
```

Then expose it: clicking any transaction shows its full decision path — matched on UTR,
variance −8.2%, rule R-14 fired, derived from a resolution on batch 2, guardrails passed.
This one screen answers "would you trust it" better than anything you can write.

---

## 7. Batch proposal cards

The user-facing shape of all the above. When an active rule matches multiple rows in a
new batch, present one card, not N exceptions:

> **Myntra commission billing at 24.2%, your master rate says 22%.**
> Explains 14 rows, ₹8,340.
> Learned from your resolution on 12 July.
> [ Accept all ] [ Review individually ] [ Not this time ]

- Accept resolves all linked rows and applies the rule's action (e.g. updates the
  ledger rate).
- Review opens the rows individually.
- Not this time records a negative observation against the rule, which affects its
  precision and can trigger retirement.

## 8. Rules page

List every rule: what it is in plain words, state, support count, live precision, when
it last fired, and the human resolution it descended from. Editable and disableable.

Include the corrigibility path: narrowing a rule's variance band because it is
over-matching, and the system respecting it. Ten seconds of video that proves the human
is still in charge.

---

## 9. Wire up the harness

Checkpoint 2 left plumbing for these. Fill them in:

- Auto-resolution precision against ground truth, per batch
- Abstention rate and **abstention correctness** — did it correctly refuse on the
  held-out categories in batches 7 and 9?
- Rules learned / promoted / retired per batch
- Rupees auto-resolved vs rupees escalated
- Tokens and rupee cost per reconciled transaction
- The review-rate series for the chart, as a percentage

---

## Done conditions

- [ ] Review rate declines across the ten batches **and** precision holds. Both, or the
      checkpoint is not done.
- [ ] The curve plateaus above zero. If it reaches zero, either your one-offs are missing
      or a guardrail is not firing.
- [ ] Held-out categories in batches 7 and 9 are correctly **not** auto-resolved.
- [ ] At least one rule retired itself, and you can point to why.
- [ ] The near-miss row from checkpoint 1 either got caught by a guardrail or shows up in
      the precision number as a real miss. Either outcome is fine; a silent pass is not.
- [ ] No rule in the store contains a transaction id — enforced by a test.
- [ ] Provenance chain complete for every auto-resolution; decision-path view works.
- [ ] LLM calls confined to `pipeline/llm/`, cached, deterministic on rerun.
- [ ] Batch proposal cards and rules page plugged into the existing UI.

---

## Do not

- Do not skip shadow mode to make the curve steeper. The lag between learning and
  automating is what makes it believable.
- Do not tune the guardrails to raise the auto-resolution rate. If the rate is modest and
  the precision is high, that is a better result and a better story.
- Do not write your resolution reasons in rule-shaped language.
- Do not hide the retired rule. It is evidence the lifecycle works.
