# TallyTrace — context pack

A single-file briefing: what the contest asked for, how the work is judged, and what
this project actually is, does and measures. Written to be pasted into an LLM as
context.

**Every figure below comes from `make score` over the ten shipped batches** and was
checked against `data/score.json` at the time of writing. If a number here disagrees
with that command, the command is right and this file is stale. Nothing in this file
is estimated, projected or rounded up.

---

# PART 1 — THE CONTEST

## Track 04 — AI Finance Controller

> **Run the books and the cash position**
>
> Build an agent that closes one finance-ops loop across a 50+ record batch of
> synthetic data, reporting its match rate and the exceptions it could not resolve.

**Why now (as stated by the brief).** The 2026 builder consensus: verification
capacity, not generation speed, is the bottleneck. Reconciliation, settlement and
forecasting are still done by hand.

**Example directions offered.** Multi-source reconciliation · Settlement Q&A agent ·
Forward cash forecaster · Tax-line matcher.

**The bar, verbatim.**

> Throughput plus measured accuracy plus an honest exception list. One cherry-picked
> match proves nothing.

Three things, not one. A build that matches well but cannot say what it failed on
does not clear this bar.

## The judging heuristic

> **We read the work, not the resume.** We look at how you think, build and solve
> problems.

| Criterion | What it asks |
|---|---|
| **Problem taste** | did you pick something that actually matters |
| **Build quality** | does it run, is it structured, would you trust it |
| **AI judgment** | the right tool in the right place, **and where you chose not to use one** |
| **Failure recovery** | what broke, and what you did about it |

Two of these four reward things most submissions hide: the places a model was
deliberately *not* used, and the things that went wrong. This project is organised
around that.

---

# PART 2 — THE PROJECT

## What it is, in one paragraph

TallyTrace is a reconciliation agent for a multi-channel Indian apparel seller
(Amazon, Flipkart, Myntra, own website, offline POS). It reconciles three sources —
the platform settlement report, the bank statement and the seller's own internal
ledger — surfaces what it cannot match, learns from how a human resolves those
exceptions, turns the ones somebody else owes into tracked claims with a filing
deadline, and answers plain-language questions from a fixed metric registry that
refuses what it cannot compute.

## Headline numbers

| | |
|---|---|
| Records reconciled | **2,409** across three sources; 1,210 settlement rows |
| Auto-match rate | **69.17%** of settlement rows (837 / 1,210), exact keys + explicit tolerances |
| Closed without a human | **12.07%** of settlement rows (146 / 1,210) |
| Human decisions per batch | **22.03% → 6.08%** across ten batches |
| Auto-resolution precision | **98.63%** over 146 scored resolutions |
| Rules learned / active / retired | 31 / 9 / **1 (retired itself)** |
| Correct abstention on held-out causes | **100%** on both, on first sight and ever |
| Claims opened / recovered / expired / open | 57 / **26** (₹48,441.58) / 11 (₹17,516.77) / 20 (₹35,252.95) |
| Claim recovery rate on settled claims | **70.27%** |
| Questions asked / mapped / declined | 11 / 8 / **3** (1 clarification, 2 refusals) |
| Model spend | ₹160.13 total, **₹0.13 per settlement row** |
| Open, unresolved, itemised | **666 exceptions, ₹498,604.90** — every one listed in `EXCEPTIONS.md` |
| Tests | 379 test functions, 411 cases |

## The problem being solved

Across ten weeks the books record **₹34,05,263.59** of orders. **₹21,75,260.07**
reached the bank. The gap is **₹12,30,003.52**.

**Most of that gap is entirely legitimate — and that is exactly why the illegitimate
part goes unfound.** A wrong deduction sitting among a hundred correct ones does not
look wrong. The gap is made of:

| Cause | Why it happens | Rows |
|---|---|---|
| Commission rate moved | platform changed the category rate; the master rate sheet did not | 129 |
| RTO reversals | full order value clawed back 1–3 weeks after it settled at full value | 54 |
| Settlement lag | the sale is in one weekly file, the payout in another | 48 |
| GST / TCS / TDS | 18% on commission, 1% under s.52, 0.1% under 194-O — three separate cycles | — |
| Weight-dispute holds | reported as sold, commission retained, nothing paid out. Held, not lost | claim |
| Short payments | paid under the net due, nothing on the report to account for it | claim |

Effective take rate across the corpus: **29.92% on Myntra, 2.23% on the own website.**
Almost none of that difference is visible on an order page.

The last two rows are why a **claims queue with a clock** matters more than a chart.
Amazon's SAFE-T window is 30 days from the event; a TCS discrepancy must be raised
before the 10th of the following month or the GSTR-8 correction misses its return.
In this corpus **₹17,516.77 across 11 claims expired unrecovered** — a number that
exists only because something was counting the days.

## Positioning — no novelty claim

Deterministic reconciliation is solved. **Unicommerce** and **EasyEcom** already do
multi-channel payment reconciliation for Indian sellers with real marketplace
integrations. **BlackLine** has run account reconciliation at enterprise scale for
two decades and publishes auto-certification rates. **Numeric** and peers do
AI-assisted close work including learning from a controller's resolutions.

**The claim is about segment, not invention.** That stack exists at enterprise price
points and multi-month implementations. It does not exist for a seller doing a few
crore across four channels who is reconciling in a spreadsheet and finds out about a
SAFE-T window after it has closed. That is a distribution argument, not a technical
one.

**Benchmark, stated unflatteringly on purpose.** BlackLine reports **43–85%**
auto-certification. This build closes **12.07%** of settlement rows without a human.
The comparison is not like for like in either direction: BlackLine's number covers
whole reconciliations that tie out under a materiality rule — the equivalent here is
the **69.17%** auto-match, not the 12.07%. The 12.07% is narrower and harder: rows
that already failed a deterministic match and were then closed by a rule induced from
a human's sentence. Quoting 69.17% against 43–85% would be the flattering comparison
and would compare two different things.

---

# PART 3 — FEATURES, IMPLEMENTATIONS, OUTCOMES

Five capabilities. Each is stated as what it does, how it is built, and what it
measured.

## 1. The matcher — deterministic reconciliation

**Feature.** Reconciles settlement report, bank statement and internal ledger. Every
input row lands in exactly one of `matched` / `variance` / `unmatched` / `quarantined`
with a machine-readable reason code.

**Implementation.**
- **No fuzzy matching anywhere.** Order level is an exact key; bank level is an exact
  key plus an explicit rupee tolerance. A test greps the tree for `recordlinkage`,
  `rapidfuzz`, `fuzzywuzzy`, `difflib` and fails on a hit. Rationale: a
  0.87-confidence match is not a match, it is money booked against an order nobody
  chose, with an audit trail reading "the algorithm was fairly sure".
- **N:1 bank join.** 150 payout groups, 147 tying out within ₹1.00; the widest
  aggregates 72 settlement rows. When a group does not tie, a *bounded* residual
  search (greedy pass, then short combinations, capped in config) names the rows the
  credit does not account for. When it finds nothing it reports shortfall + full
  candidate list + `search_exhausted: true`. **It never widens the search until
  something fits.**
- **Tolerances come from the ledger, not a constant.** The value band is a percentage
  of the order's own `expected_fee`, with rounding tolerance as a floor — so a stale
  rate produces a variance proportional to the order, which is what makes fifty stale-
  rate exceptions look like one rule rather than fifty coincidences.
- **Late is not lost.** A settlement inside `date_window_days` (21) is a cycle, not
  missing money, and is never flagged. Past the window it is flagged as a variance
  with ₹0.00 impact — the money is right, only the timing is wrong.
- **Quarantine, never drop.** Rows are validated one at a time with a named reason
  (`malformed_unparseable_date`, …), raw text kept. All 5 planted corruptions land there.
- **Purity.** Nothing under `pipeline/matcher/` opens a file, reads a clock or draws a
  random number — asserted by a test. No pandas anywhere: a pandas column of `Decimal`
  is an object column one careless operation away from float coercion.
- **Money is `Decimal` everywhere.** `pipeline/models.py` raises on a float in any
  money field, on construction *and* on assignment. Config YAML floats parse as
  `Decimal`. Rounding is half-up, not banker's.

**Outcome.** **69.17% auto-match** across 1,210 settlement rows. Batch 1 matches
81.36%; batch 10 matches 58.01% — the corpus behaving as designed, because batch 10
receives every cross-batch reversal planted in batches 8–9 and emits none of its own.
Matcher precision: **658 of 666 findings trace to an injection**; the other 8 are the
quarantined rows and the bank side of the duplicates.

## 2. The learning loop — the repeated decision

**Feature.** A bookkeeper clears an exception and explains it in their own words. That
sentence becomes a rule. Next batch, a row of the same shape is recognised and offered
back. **There is no rule builder in the product** — nobody writes a rule, they explain
a fix.

**Implementation — eight steps per batch, and the order is the product.**
1. Reconcile. Nothing downstream can change a bucket.
2. Build the queue. Verdicts group into **cases** — a wrong rate produces a verdict on
   the settlement row and one on the ledger row, and a bookkeeper works that once.
   New findings only.
3. Hypothesise. Each readable case gets a cause from the model, constrained to a
   **frozen 16-cause enum in the JSON schema**, not in the prompt text.
4. Decide. Active rules may auto-resolve subject to guardrails; shadow rules predict
   and log; unmatched cases go to a human untouched.
5. Card decisions. Apply what the operator did with last week's proposals.
6. Claims. Everything routed to a counterparty goes to the register.
7. Resolve. The operator's free text is read, shadow predictions are judged against
   what the human actually said, and new rules are induced.
8. Advance. Every rule's lifecycle state is recomputed.

**Step 4 before step 7 matters: a rule must predict before it is told the answer, or
its precision measures nothing.** Step 8 last, so a rule promoted this week starts
firing next week rather than retroactively.

**Lifecycle.** `proposed` → `shadow` → `active` → `retired`. A rule induced in batch 1
shadows batch 2 and fires from batch 3 at the earliest. Promotion needs both minimum
confirmations **and** minimum precision — volume alone is not evidence, and a shadow
prediction nobody has ruled on is not a confirmation.

**Guardrails run *after* the rule matches, and they override it.** That ordering is
the design position worth defending: a rule's confidence is an opinion about a
pattern; a threshold is a decision about risk, and **the opinion never wins**. All
three are evaluated every time, pass or fail — a short circuit would lose the record.

| Guardrail | Source | Effect |
|---|---|---|
| `max_variance_inr` | `config/thresholds.yaml` | above **₹500** by default, never auto-resolve — settable per cause and per channel |
| `never_auto_resolve_causes` | same | TCS, TDS, chargebacks, whatever a rule believes |
| resolution class | `config/causes.yaml` | `tax_review`, `investigate`, `counterparty_claim` are always human |

The third includes claims deliberately: closing a row someone else owes money on is
not a resolution, it is a write-off nobody authorised.

**Ceilings are set by the business.** `max_variance_inr` is a default; finance sets
scoped ceilings under it. Most specific wins; a tie between two equally specific
scopes goes to the **stricter**; a scope that could never fire is a load error; a
ceiling is not a master switch (it cannot lift a blocked cause or class); and it
cannot be invisible — the governing ceiling and who set it are written into the
decision path of every resolution it touched.

**Provenance.** Every auto-resolution records the rule, its state when it fired, the
resolution it descends from, the operator who wrote it, the proposed cause, and every
guardrail evaluation. The UI renders the record verbatim.

**Outcome.**
- **Human decisions per batch: 22.03% → 6.08%** across ten batches, plateauing above
  zero (one-off causes are planted in every batch, including the last two).
- **Auto-resolution precision 98.63%** over 146 scored resolutions. Series by batch:
  100, 100, 88.89, 100, 100, 95.83, 100, 100 (batches 3–10).
- **31 rules learned, 9 active, 1 retired itself.**
- **₹8,203.42 auto-resolved against ₹490,401.48 escalated.** The system automates
  volume and escalates value. Both figures are printed side by side so the ratio
  cannot be quietly inverted.
- **Correct abstention 100%** on both held-out causes: `promo_cofunding_deduction`
  (first seen batch 7, 10 cases, 0 ever auto-resolved) and `chargeback_deduction`
  (first seen batch 9, 6 cases, 0 ever auto-resolved). Correct abstention is the
  hardest behaviour to fake.

## 3. The claims queue — money somebody else owes

**Feature.** Exceptions routed by `resolution_class` to a counterparty become tracked
claims with a filing deadline, a drafted message, and automatic closure when the money
arrives.

**Implementation.**
- **Two genuinely different clock shapes.** A **duration** (`opened_at + 30 days` for
  Amazon, Flipkart, Myntra) and a **statutory cutoff** (a TCS discrepancy must be
  raised before the 10th of the month *after* the one it arose in). A claim opened on
  the 2nd has 39 days; one opened on the 28th has 13. Forcing that into a
  days-remaining model would put a wrong number on screen for eleven months of the
  year. `CLM-0005` is that case: ₹19.51, expired 2025-07-10.
- **A platform with no configured window gets no clock at all**, sorted last, labelled
  "no configured filing window". A default would be a countdown no agreement backs, in
  a queue whose entire value is its countdown.
- **Sorted by expiry, never by creation date.** A list ordered by when it was raised
  buries the one that stops being recoverable on Thursday.
- **The draft is fenced.** The model produces three short strings — subject, factual
  statement, request — and **the schema rejects any numeral in any field**. Every
  figure (order reference, settlement rows, expected vs received, amount claimed,
  filing deadline) is substituted from the matcher's own verdicts. A test takes the
  drafts a real run produced and asserts every numeric token traces back to the claim
  or its evidence rows. Rationale: a rupee figure a language model typed is a rupee
  figure nobody computed, and one wrong rupee is the whole claim.
- **The prompt is keyed on `(platform, cause)`** and carries no order id and no amount,
  so 25 Amazon missing-settlement claims share one cached answer and the model never
  sees a transaction.
- **Auto-close on recovery.** When a later batch carries a credit settling an open
  claim, the register links it and the claim moves to `recovered` — matched the way
  every other match here is made: **same `order_id`, money in, amount within the
  rounding tolerance.** One row closes at most one claim. **The row's description is
  deliberately not used**: the generator writes `CLAIM REIMBURSEMENT ord_000081` on
  planted rows, and matching that string would close 5 of 5 and measure nothing but
  the fixture.
- **Closing a claim does not reduce the review rate.** The credit that closes it is
  still a row somebody has to book.

**Outcome.**
- **57 opened, 26 recovered (₹48,441.58), 11 expired (₹17,516.77), 20 still open
  (₹35,252.95).** Recovery rate on settled claims **70.27%**. Open claims count as
  neither — a claim inside its window is not yet a result.
- Queue header, computed: `₹35,252.95 open across 20 claims · 3 expiring in 9 days`.
- **3 of 5 planted recovery pairs auto-close.** The other two never became claims at
  all — the reimbursement arrived while the order was still inside its settlement
  window, so the matcher never raised it. **Both are reported as misses anyway**,
  because excluding them would be marking its own homework.

## 4. The reporting surface — asking the books

**Feature.** Describe a metric in plain language; the system restates what it will
compute; nothing runs until you accept the restatement; a useful result can be pinned
and recomputes every batch.

**Implementation.**
- **A closed registry of ten metrics**, each with an id, description, unit, supported
  groupings and a pure computation function: `net_revenue_by_channel`,
  `gross_order_value`, `effective_take_rate`, `commission_share_of_gross`,
  `exception_count_by_cause`, `review_rate_trend`, `auto_resolved_rows`,
  `claim_recovery_rate`, `open_claim_value`, `rupees_expired_unrecovered`.
- **No SQL is generated anywhere**, and a test fails if `sqlite3`, `sqlalchemy`,
  `psycopg`, `pymysql` or `duckdb` is ever imported. Rationale: enterprise text-to-SQL
  execution accuracy runs roughly **21–39%** on realistic schemas and its failures are
  silent — a valid query returns a plausible wrong number and nothing on screen says
  so. A closed registry can be wrong in exactly one way (picking the wrong id out of
  ten) and that choice is shown to a human before anything runs. **The limit is the
  point.**
- **Three outcomes, only one of which is an answer.** Map to a metric, ask exactly one
  clarifying question, or refuse. The schema rejects an outcome that refuses and names
  a metric at the same time, so a refusal cannot quietly carry a result.
- **Confirm-before-compute is a property of the code**: `execute()` raises
  `NotConfirmed` on an unconfirmed plan rather than assuming.
- **A pin stores a metric id and its parameters — never a number** — plus the question
  it came from. `tests/test_pins.py` monkeypatches `LlmClient.__init__`,
  `LlmClient.ask`, `client_from` and `ResponseCache.get` to raise, then recomputes all
  five pinned metrics anyway. **The model is present at the moment of definition and
  absent from every run afterwards.**

**Outcome.** Of 11 logged questions: **8 map, 1 clarifies, 2 refuse.**
- A clarification: *"How are our fees trending?"* — "fees" means two different registry
  metrics that differ by several points on every channel, so it asks once and computes
  nothing.
- A refusal: *"Which of our SKUs are least profitable?"* → "This reconciliation holds
  orders, settlements and bank credits. It has no product master and no cost of goods,
  so profitability per SKU cannot be computed here at all — not approximately, and not
  from an adjacent figure."

## 5. The harness — measuring it honestly

**Feature.** Scores the whole pipeline against a ground-truth answer key and writes
the exception list.

**Implementation.**
- **Built *before* the learning loop, not after.** Built afterwards a harness becomes
  a thing that confirms what you hoped; built first it catches what went wrong — four
  of six defects in `FAILURES.md` #7–#14 were found by pointing it at code that passed
  all of its own tests.
- **One writer and one reader of the answer key.** `generator/main.py` is the only
  module that may name the truth path on the way in, `harness/truth.py` the only one on
  the way out — and a test fails if anything under `pipeline/` so much as mentions the
  path. Two lines answer "who could have touched the answers?"
- **Silent clears report headroom, not just a count.** A count cannot tell you a
  tolerance band is wrong, since every silently cleared row is inside a band by
  definition. So the harness reports the **tightest headroom**: `rounding_variance`
  clears with as little as **₹0.16 under the ₹1.00 floor**.
- **New findings vs aged ones.** An order overdue in batch 5 and never paid is
  correctly unmatched in batches 5–10; three missing settlements would otherwise appear
  as 39 queue entries. `harness/aging.py` splits them — the split lives in the harness
  because the matcher is a pure function of one batch and cannot know it has seen a row
  before.
- **Live precision vs true precision, printed adjacent.** Live comes from what the
  operator's later resolutions said; true comes from the answer key the pipeline never
  sees. R-05 fires on the planted near-misses, the operator confirmed them too, so its
  live precision reads 100.00% against a true precision of 97.44%. That gap is the
  entire difference between 98.63% and a suspiciously clean 100%.

**Outcome.** `RESULTS.md` (harness output verbatim), `EXCEPTIONS.md` (**666 open
exceptions itemised**, ₹498,604.90), `data/score.json` (every number, every amount a
string), all regenerated on every run and all committed.

---

# PART 4 — AI JUDGMENT

## Used, in four places — all natural-language boundaries

| Job | Module | Output schema | What constrains it |
|---|---|---|---|
| Hypothesis — *why did this row fail to match?* | `llm/hypotheses.py` | `Hypothesis` | the 16-cause enum, inlined in the JSON schema |
| Rule induction — *what does this sentence mean as a predicate?* | `llm/induction.py` | `InducedRule` | **no field exists for an identifier** |
| Claim narrative — *the words around the evidence* | `llm/drafts.py` | `ClaimNarrative` | **no numeral permitted in any field** |
| Intent mapping — *which registered metric answers this?* | `llm/intent.py` | `MetricIntent` | the ten registered metric ids |

Every one is a forced tool call at `temperature=0` whose `input_schema` carries the
constraint — so the constraint is in the request, not in the prose. A reply that
violates it raises `SchemaViolation`. **There is no fallback branch**, because a
fallback is how an invented cause reaches a bookkeeper wearing a confidence score.

**One of the four also runs in the deployed build.** `ui/api/ask.js` is intent mapping
ported to a Vercel serverless function against **Gemini**, so a deployed page can answer
a question outside the committed fixtures. Same contract: one id from the frozen ten, or
decline; never computes, never queries, never sees a row; and its response schema
*cannot express a filter*, because the deployed build holds whole-corpus results only.
The three outcomes are validated server-side exactly as `MetricIntent` validates them —
a `refuse` that also names a metric is rejected, not repaired. Fixtures are tried first,
so only an unasked question reaches it, and the answer is labelled **mapped live** on
screen. `tests/test_ui_data.py` fails if its mirrored registry drifts from
`pipeline/metrics/registry.py`. **No scored number in this file depends on it** — `make
demo` runs `--offline` and refuses the network even with a key set.

## Refused, in six places — each for a reason

| Not used for | Because |
|---|---|
| **Matching** | money does not want probabilistic matches; a 0.87 match is a liability |
| **Applying a learned rule** | induction is language work; application is comparing numbers |
| **Deciding what may be auto-resolved** | thresholds are code and run *after* the rule, so they can only take the decision away |
| **Computing any metric** | the registry computes; the model only selects |
| **Generating SQL** | 21–39% execution accuracy with silent failures |
| **The claims clock** | no configured window means no deadline, rather than a plausible default |

## The enforcement — tests, not paragraphs

Nine tests in `tests/test_boundaries.py` plus one in `tests/test_llm.py`. No mocking —
greps and imports over the source tree, `tools/` included.

| Assertion | What it prevents |
|---|---|
| `anthropic` imported only under `pipeline/llm/` | a model call anywhere else |
| `pipeline/rules/` cannot import the client | rule application becoming probabilistic |
| `pipeline/metrics/` cannot import the client | a metric that asks instead of computing |
| `pipeline/claims/` cannot import the client | the deadline clock depending on an API |
| no fuzzy-matching library anywhere | probabilistic linkage of money |
| no SQL engine anywhere | text-to-SQL arriving through the back door |
| `pipeline/` never names the answer-key path | a matcher that can see the answers |
| `harness/truth.py` is its only reader, `generator/main.py` its only writer | a two-line answer to "who could have touched the answers?" |
| no rule may contain a transaction id | enforced twice — schema has no field, plus a free-text checker |

## Cost and caching

400 readable exceptions ask **45 distinct questions** — 89 cases are "Myntra, fee
variance, short, 8.8% over" with nothing between them but the paise. The prompt is
built from a normalised signature so identical questions collapse to one cached answer
**by construction**. A hypothesis that *differed* between two numerically identical
rows would be non-determinism, not insight.

117 responses cached to `data/llm_cache/`, keyed by a hash of model + system prompt +
user prompt + output schema. **A cache hit is billed at the cache-read rate rather
than as free** — the first run paid for the answer, and a per-transaction cost that
only counts cold runs is not a cost. Total **₹160.13, ₹0.13 per settlement row.**

---

# PART 5 — FAILURE RECOVERY

## A rule that learned something false and took itself out

**R-07** was induced in batch 2 from a note saying late claw-backs are returns coming
back — true on Flipkart, false on Amazon. The note generalised further than the
evidence did. It predicted on six late deductions in batch 3, the operator's own
Amazon resolutions contradicted it, and it **retired itself at 40.00% precision over
five judged observations**. Nobody scheduled that review.

**The note that caused it was not rewritten afterwards.** It is what a real bookkeeper
would have typed, and a corpus edited to stop producing bad rules would prove nothing.

## The two it still gets wrong

Two Myntra rows are planted with a surface signature **identical** to the learnable
stale-rate rule — same channel, same ledger rate, same charged rate, same variance
band — but a true cause of `short_payment_unexplained`, routed to claims rather than
the learning loop. The rule fires on them and is wrong.

**The operator was fooled by the same two rows**, which is why live and true precision
are printed side by side. Those two rows are the entire difference between 98.63% and
100%.

## The ceiling trap that was one commit from shipping

`make ceilings` scores the corpus at every candidate ceiling and prints **two**
precision series:

| Ceiling | Closed | Wrong | True % | Live % | Gap |
|---|---|---|---|---|---|
| ₹500 (shipped) | 146 | 2 | **98.63** | 98.63 | 0.00 |
| ₹600 | 149 | 2 | **98.66** | 98.66 | 0.00 |
| ₹700 | 155 | 4 | 97.42 | 98.71 | 1.29 |
| ₹1,000 | 162 | 7 | 95.68 | 98.77 | 3.09 |
| ₹2,000 | 202 | 21 | 89.60 | 99.01 | 9.41 |
| ₹3,000 | 233 | 30 | 87.12 | **99.14** | 12.02 |

**Live precision rises with the ceiling and true precision falls.** The marginal rows
are ones a rule and an operator get wrong in the same direction, and the bigger the
row the more often they agree wrongly — so a ceiling chosen on live precision alone
rises forever *while the system reports it is getting better at it*. That is
`FAILURES.md` #40. ₹600 is the actual frontier; the build ships at ₹500 because that
is where the seller drew the line.

## The chart that looked like the finding I was hoping for

The fees chart originally showed absolute rupees. Batch sizes grow from 59 to 181
settlement rows, so an absolute fee line rises whatever the platforms do. The first
version climbed from 5% to 86% and was **entirely an artifact of taking the
denominator off the wrong file**. It is now effective take rate as a percentage of
gross, which reads `18.03, 15.98, 19.61, 19.04, 17.24, 16.81, 18.98, 17.72, 16.69,
15.61` — flat, which is the truth about this corpus. `FAILURES.md` #30. A wrong chart
that looks like the finding you were hoping for does not get checked.

`FAILURES.md` has been kept by hand since checkpoint 1 and holds 40 entries.

---

# PART 6 — LIMITATIONS, STATED BEFORE A JUDGE STATES THEM

- **The claims queue over-claims on late payouts, badly.** 27 of 57 claims are
  `missing_settlement_row` and the answer key confirms only **4**; the other 23 were
  settlements that were merely late. **Overall claim attribution is 34 of 57 = 59.65%.**
  Mitigation is measured, not argued: **14 of those 23 closed themselves** when the
  money arrived, with no operator ever filing them. The bias is deliberate — chasing a
  late payout costs a claim that closes itself; not chasing a genuinely missing one
  costs the whole payout once the window shuts. It is the least flattering number in
  the build and it is printed in the report, in `EXCEPTIONS.md` and in the UI.

  Per-cause attribution: chargeback 6/6, promo co-funding 10/10, short payment 4/4,
  TCS 2/2, weight dispute 8/8, **missing settlement 4/27 (14.81%)**.

- **The strict row-level review rate does not fall.** It ends 4 points *above* batch 1
  (18.64% → 22.65%), and what the matcher alone leaves rises 18.64% → 41.99%. Only the
  *decision* rate falls (22.03% → 6.08%). All three series are printed. `FAILURES.md`
  #21 sets out the three shortcuts that would have produced a falling curve — widening
  a tolerance, counting a guardrail hold as resolved, quietly changing the denominator
  — and why each was refused.

- **Token counts are estimated, not metered.** The `data/llm_cache/` fixtures were
  produced by Claude Opus reading each rendered prompt through a coding session rather
  than over the HTTP Messages API, because the build machine had no API key. Request
  text and output schema are byte-identical to what the client sends; only transport
  differs. Every entry carries `source: "transcript"` and every report says **TOKEN
  COUNTS ARE ESTIMATED** where the number is printed. Recording zero would report a
  model-backed pipeline as free, which is more misleading than an approximate figure.

- **The data is synthetic and I wrote both sides of it.** Mitigations are structural —
  the pipeline cannot read the answer key, operator notes were written against each
  case's *shape* rather than against the key, and near-misses were planted so a rule
  would fire and be wrong — but a synthetic corpus cannot tell you what real Amazon
  settlement files do at the edges.

- **The UI renders a completed run.** No server behind it. "Accept all", "Not this
  time", "Narrow the band" and "Disable" state what they *would* record rather than
  writing back to `data/resolutions.json`. The code paths behind those controls are
  real and are driven by tests; only the write-back is absent. The queue carries that
  sentence at the top.

- **`written_off` is a claim status nothing in this corpus reaches.** In the schema and
  implemented; writing off needs an operator action the log has no record of. Left in
  and said plainly rather than deleted.

- **Six claims carry no deadline** — website chargebacks, for which no filing window is
  configured. Shown last with "no configured filing window". In reality a card
  chargeback has a representment window; the honest statement is that this build does
  not know it.

- **Two of five planted recovery pairs never became claims** (the reimbursement arrived
  inside the settlement window, so nothing was raised). Reported as misses anyway.

- **The corpus is closed and thin at both edges.** Every order settles inside the ten
  batches, which is what lets the clean base reconcile at exactly 100%; the cost is
  that batch 1 carries a large opening book (173 ledger rows) and batch 10's ledger is
  small (24 rows), so cross-batch causes are under-represented at both ends.

- **No video is recorded.** `VIDEO.md` holds the shot list and script with every number
  sourced to a command, but the recording has not happened.

---

# PART 7 — HOW TO RUN AND VERIFY IT

```bash
make venv           # .venv + pinned dependencies
make demo           # generate, reconcile, learn, claim, score, build the UI data
make reproduce      # run the whole demo twice and diff the artifacts
make check          # clean-base verification, full test suite, mypy

make claims                                            # the queue, sorted by expiry
make ask q="How much money are we still chasing, by platform?"
make whatif ceiling=3000                               # rescore at a different ceiling, writing nothing
make ceilings                                          # score every candidate ceiling
cd ui && npm install && npm run dev                    # the dashboard
```

**No API key is needed and none is used.** `make demo` runs `--offline`, which refuses
the network *even with a key set*, so what it proves is that the committed fixtures and
the seed are sufficient — not that a particular shell happened to have no key in it.

**Reproducibility.** `make generate` is seeded and byte-identical every run;
`tests/test_determinism.py` asserts it three ways (two fresh runs match, a fresh run
matches the committed data, a different seed produces a different world — so
determinism comes from the seed rather than from the generator ignoring it).
`make reproduce` does the proving rather than asserting it: two complete runs from
scratch, then a byte comparison of `data/score.json`, `data/rules.json`,
`EXCEPTIONS.md` and `ui/public/tallytrace.json`. All four match. `data/score.json` is
byte-identical apart from a single `timings` block carrying `"reproducible": false`,
because wall clock is a required metric and a deterministic artifact, and those cannot
both be true of the same numbers.

**Repo layout.**

```
config/     thresholds.yaml, causes.yaml, channels.yaml, generation.yaml, pricing.yaml
data/
  generated/        batch_01 .. batch_10, three CSVs each
  truth/            the answer key — PIPELINE MUST NEVER READ THIS
  llm_cache/        cached model responses, committed: this is what makes a rerun free
  resolutions.json  the operator's own words; the root of every provenance chain
  questions.json    what the operator asked the reporting surface
  pins.json         pinned metric definitions — ids and parameters, never numbers
generator/  synthetic world, injectors, writer, clean-base verifier
pipeline/   models.py, config.py, loader.py, run.py, cases.py, learn.py
  matcher/    deterministic matching
  llm/        the only place a model may be called
  rules/      induction targets, lifecycle, predicates, guardrails, provenance
  claims/     routing, the two deadline clocks, recovery matching, drafting
  metrics/    the metric registry, the corpus it computes over, pins
harness/    scoring: reads data/truth, which the pipeline never does
tools/      fixture and artifact builders, the ask CLI, the reproducibility check
ui/         React dashboard, fed by one scored run
tests/      379 test functions, 411 cases
```

**The UI.** Seven screens — Dashboard, Review Queue, Transactions, Claims, Rules, Ask,
Report & Settings — all fed by one scored run (`ui/public/tallytrace.json`), so no two
screens can disagree with each other or with the terminal. Screens are addressable:
`#claims`, `#review?week=6`, `#ask?q=<question>&yes=1`. The app opens on the last
batch, which is the week whose books are being closed.

---

# PART 8 — THE DATASET

Ten weekly batches built as **clean base + injected troubles**, with a separate
ground-truth key recording every injection (affected row ids, true cause, true rupee
impact, correct resolution class — and no claim about whether a matcher *should* catch
it, because that is the harness's finding, not the dataset's assertion).

| | |
|---|---|
| Settlement rows per batch | 59 → 181, monotonic, every batch over the 50-record floor |
| Bank credits | 150, largest aggregating 72 settlement rows (a real N:1 join) |
| Ledger rows | 1,049 orders |
| Injected troubles | 371 affected rows across 16 causes, ₹464,246.13 of true impact |
| Malformed rows | 5, spread across batches 2, 4, 6, 8, 9 |
| Planted claim recoveries | 5 reimbursements landing 2–4 batches after the loss |

**The clean base reconciles at 100% before anything is injected.** `make verify-clean`
proves it, written against the emitted world rather than reusing the code that built
it, so a builder bug cannot cancel itself out.

**The frozen 16-cause enum.** `commission_rate_stale`, `commission_slab_change`,
`fee_mismatch_other`, `rto_reversal_later_cycle`, `refund_timing_lag`,
`settlement_lag_crossing_batch`, `rounding_variance`, `duplicate_settlement_row`,
`tcs_timing_mismatch`, `tds_timing_mismatch`, `weight_dispute_hold`,
`missing_settlement_row`, `short_payment_unexplained`, `chargeback_deduction`,
`promo_cofunding_deduction`, `bank_credit_unmatched`.

## Deliberate difficulties — removing one makes the demo look better and the system worse

- **Held-out causes.** `promo_cofunding_deduction` does not appear before batch 7,
  `chargeback_deduction` not before batch 9. The system must correctly *fail* to
  auto-resolve these on first sight.
- **Two near-misses** (batches 5 and 8), described above. The false positive to
  feature, not to remove.
- **One-off causes in every batch**, including the last two. This is why the review
  rate plateaus above zero — a curve to zero reads as scripted, because it is.
- **Batch 1 is not flattered.** Its trouble rate is genuinely high; that is the
  starting point the decline is measured from.
- **Both refund sign conventions.** Amazon, Myntra and the website negate the amount;
  Flipkart and the POS report a positive amount against the debit column.
- **Paise drift** inside the rounding tolerance, which should cost nobody any attention.

## The operator

For this build the author is the human. `data/resolutions.json` holds **195
resolutions** written the way a bookkeeper writes them — *"Myntra is billing 27.2% on
these but our master rate sheet still says 25%. Their category manager flagged in the
January mailer that outerwear moved up a slab."* — not in the shape the rule engine
wants. **Text engineered to induce cleanly would have tested nothing**, and one
deliberately loose note is what produces the retired rule.

The operator policy is stated in `tools/write_resolutions.py`: work the whole queue for
the first three batches, then work anything whose *shape* is new and spot-check two of
each familiar shape. The spot checks are what keep a promoted rule's live precision a
live number — without them a rule promoted in batch 3 could never be judged again and
could not retire, which would make the lifecycle decorative.

---

# PART 9 — RULES FOR TALKING ABOUT THIS PROJECT

Constraints for any LLM generating copy, a script or a summary from this file.

**Do not:**
- State any number not in this file or produced by `make score`, `make claims` or
  `make ask`.
- Say "AI-powered" about anything other than the four call sites in Part 4. The
  deployed Gemini mapper is one of those four (intent mapping) running in a second
  place — it is not a fifth job and it computes nothing.
- Say the review rate falls. The **decision** rate falls (22.03% → 6.08%); the row rate
  does not, and both are printed.
- Say five of five planted recoveries auto-close. **Three do.**
- Quote 69.17% against BlackLine's 43–85%. The comparable figure is 12.07%, and it is
  lower.
- Claim novelty. The claim is about segment.
- Describe the token counts as metered. They are estimated, from character length.
- Imply a video exists.

**Do:**
- Lead with the least flattering number when there is a choice — 59.65% claim
  attribution, the row rate that does not fall, ₹17,516.77 expired unrecovered.
- Name the guardrail whenever the automation share comes up: the system automates
  volume and escalates value **on purpose**.
- Attribute every automated decision to the human sentence it descends from.
- Treat "where a model was refused" as a feature, not an omission.
