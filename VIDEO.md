# VIDEO

The five-minute walkthrough: shot list, script, and the command behind every number.

**Nothing in the script is typed from memory.** Each beat names the command that puts the
figure on screen, so the voiceover can be checked against a terminal rather than trusted.
If a number here disagrees with `make score`, `make score` is right and this file is
stale.

**Status: not recorded.** This is the script, not a transcript. It is listed under
Limitations in the README rather than implied to exist.

---

## Before recording

```bash
make clean && make venv && make demo && make reproduce
cd ui && npm install && npm run dev
```

`make reproduce` runs the whole demo twice and diffs the artifacts. Do it before
recording, so that the claim made in beat 1 has been checked that morning.

---

## Beat 1 — the rupee gap, with a real number *(0:00 – 0:45)*

**On screen:** the terminal, `make demo` finishing.

> A seller doing a few crore across Amazon, Flipkart, Myntra, their own site and a shop
> counter looks at two numbers every month and they do not agree. Here are ten weeks of
> that business: two thousand four hundred and nine records across three sources.
>
> Four hundred and ninety-eight thousand, six hundred and four rupees are in question
> across six hundred and sixty-six open exceptions. That is not fraud and it is not one
> bug. It is commission on a rate that moved, GST on that commission, one percent TCS,
> point one percent TDS, returns clawed back three weeks after the sale settled, payouts
> held against a weight dispute, and short payments with nothing on the report to explain
> them.

**Source:** `make score` → HONESTY section. `666 open exceptions, ₹498604.90 in question.`

---

## Beat 2 — what already exists, named *(0:45 – 1:30)*

**On screen:** the README's "What already exists" section.

> Deterministic reconciliation is solved. Unicommerce and EasyEcom already do
> multi-channel payment reconciliation for Indian sellers with real marketplace
> integrations. BlackLine has run account reconciliation at enterprise scale for twenty
> years and publishes its auto-certification rates. Numeric does AI-assisted close work
> including learning from how a controller resolves an item.
>
> So there is no novelty claim here. The claim is about segment. All of that exists at
> enterprise price points and multi-month implementations. It does not exist for the
> seller who is reconciling in a spreadsheet and finds out about a SAFE-T window after it
> has closed.
>
> And here is my number against theirs: BlackLine publishes forty-three to eighty-five
> percent auto-certification. This closes twelve point zero seven percent of settlement
> rows without a human — and that is a narrower and harder number, because those are
> rows that had already failed a deterministic match. The comparable figure is the
> sixty-nine point one seven percent auto-match, and I am not going to quote that one
> against theirs, because it would be comparing two different things.

**Source:** README "Benchmarking against what exists". 12.07% = 146 auto-resolved rows /
1,210 settlement rows; 69.17% = 837 matched / 1,210.

---

## Beat 3 — the loop running on one batch *(1:30 – 2:30)*

**On screen:** `make learn`, then the UI review queue on batch 6.

> Batch six. Forty-one exceptions in the queue. Nineteen of them close without a human,
> worth nine hundred and seventy-seven rupees; twenty-two escalate, worth forty-three
> thousand seven hundred.
>
> That ratio is the guardrails, not a limitation. Anything above a five hundred rupee
> variance ceiling is refused automation however confident the rule is. The system
> automates volume and escalates value: eight thousand two hundred auto-resolved against
> four hundred and ninety thousand escalated across the whole corpus.
>
> And every one of those nineteen carries its whole decision path — which rule fired,
> what state it was in, whose sentence it was induced from, and all three guardrails with
> their verdicts, including the ones that passed.

**Source:** `make learn`, batch 6 line. Click any auto-resolved card in the review queue
for the decision path.

---

## Beat 4 — the two-axis chart *(2:30 – 3:15)*

**On screen:** the UI dashboard.

> Human decisions per batch: twenty-two percent of the batch in week one, six percent in
> week ten. Precision over that same period: ninety-eight point six three percent across
> a hundred and forty-six scored auto-resolutions, and it does not sag as volume grows.
>
> There are three lines on this chart on purpose. The grey one is what the matcher alone
> leaves. It does not fall — it ends four points above where it started, because batch
> ten is the biggest and hardest batch in the corpus. Reporting only the green line would
> be the easy thing to do, and the harness prints both so that a decline which came from
> widening a tolerance cannot be mistaken for one that came from learning.

**Source:** `make score` → LEARNING LOOP, `touch %` column: 22.03% → 6.08%. ACCURACY,
`review` column: 18.64% → 41.99%.

---

## Beat 5 — the deadline clock, and a claim closing itself *(3:15 – 4:15)*

**On screen:** `make claims`, then the UI claims screen.

> Thirty-five thousand two hundred and fifty-two rupees open across twenty claims, three
> of them expiring within nine days. Sorted by expiry, never by creation date — a list
> ordered by when it was raised buries the one that stops being recoverable on Thursday.
>
> Two different clocks. Amazon's SAFE-T window is thirty days from the event. A TCS
> discrepancy has to be raised before the tenth of the following month or the GSTR-8
> correction misses its return — that is a calendar date, not a duration, and it is
> handled separately for that reason. Claim five is that case: nineteen rupees fifty-one,
> expired on the tenth of February.
>
> And this is the part I care about. Claim three, order eight one, Flipkart, two hundred
> and eighty-seven rupees ninety-seven. Opened in batch two. Drafted. Filed, by a
> bookkeeper's own sentence. And in batch four a credit turns up that matches the order
> and the amount within a rupee, and the claim closes itself. Twenty-six of fifty-seven
> claims closed that way, worth forty-eight thousand rupees, with nobody clicking
> anything.

**Source:** `make claims`. `make claims --draft CLM-0003` for the letter.

---

## Beat 6 — the system getting one wrong, and the guardrail catching it *(4:15 – 4:45)*

**On screen:** the UI rules page, R-07 in red.

> Rule seven. Induced in batch two from a note that said late claw-backs are returns
> coming back, which is true on Flipkart and false on Amazon. It predicted on six late
> deductions, the operator's own Amazon resolutions contradicted it, and it retired itself
> at forty percent precision over five judged observations. Nobody scheduled that review.
>
> And the two rows that separate ninety-eight point six three from a hundred are two
> Myntra orders whose surface signature is identical to the stale-rate rule — same
> channel, same rates, same variance band — and whose real cause is a short payment
> somebody else owes us. The rule fires on them and is wrong. The operator was fooled by
> the same rows, which is why the rules page prints live precision and true precision
> side by side.

**Source:** `make score` → RULES section, R-07. FAILURES.md #22 and #24.

---

## Beat 6b — the refusal, if there is room *(optional, 15 seconds)*

**On screen:** the Ask screen, opened at
`#ask?q=Which%20of%20our%20SKUs%20are%20least%20profitable%3F`.

> One more. Ask it something the registry cannot answer and it does not produce a nearby
> chart. It says there is no product master and no cost of goods, so the question cannot
> be computed here at all — not approximately, and not from an adjacent figure. Two of the
> eleven questions in the log end that way, on purpose.

**Source:** the Ask screen. `make ask q="Which of our SKUs are least profitable?"` prints
the same refusal in the terminal.

---

## Beat 7 — one genuinely unresolved exception *(4:45 – 5:00)*

**On screen:** EXCEPTIONS.md, the "Expired unrecovered" table, and then a single row.

> End on this. Eleven claims, seventeen thousand five hundred and sixteen rupees, whose
> filing window closed with no recovery. Money the system found, chased, and then lost to
> a clock.
>
> And this one. `bank_credit_without_settlement_group` — a credit sitting in the bank
> account with no settlement report anywhere referencing that UTR. The system cannot tell
> you what it is. Neither can I. It is in EXCEPTIONS.md, itemised, with the amount, and
> it is still open.

**Source:** EXCEPTIONS.md, "Expired unrecovered" table and the three
`bank_credit_without_settlement_group` rows.

---

## Do not say

- Any number not produced by `make score`, `make claims` or `make ask`.
- "AI-powered" about anything other than the four call sites.
- That the review rate falls. The **decision** rate falls; the row rate does not, and the
  README says so in two places.
- That five of five planted recoveries auto-close. Three do.
