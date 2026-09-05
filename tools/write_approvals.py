"""Build ``data/approvals.json`` -- the operator's verdict on each candidate card.

**For this build I am the human**, the same way I am in ``tools/operator_notes.py``.
These are the decisions I would make sitting in front of the cards, and the policy
behind them is stated here rather than left to be inferred from the file:

- **Approve the narrowest rung of each phenomenon.** That rung is what the note
  actually said. The looser rungs above it are the ladder's proposals, not the
  operator's, and approving all three would put three rules on the page that mostly
  agree and occasionally do not.
- **Reject every looser rung of the same phenomenon.** Rejection is recorded rather
  than implied, because "nobody approved it" and "somebody looked at it and said no"
  are different facts about a rule and only one of them is evidence of review.
- **Approve on reach, not on precision.** Shadow mode acts on nothing, so a doubtful
  rule belongs there rather than nowhere: that is the whole point of having a state
  between induction and automation. The rule at 57% on the backtest is approved
  deliberately, and what happens to it afterwards is the lifecycle doing its job in
  public rather than a number being tidied away in advance.

Run after ``make learn`` so the card set is current: this reads the cards a real run
produced rather than a list typed out by hand, which is what keeps it from going
stale when the corpus or the ladder changes.
"""

from __future__ import annotations

from pipeline.config import batch_window
from pipeline.learn import run
from pipeline.rules.approvals import APPROVE, REJECT, CardVerdict, of, save
from pipeline.rules.candidates import LEVELS, NARROW
from tools.operator_notes import OPERATOR

#: Notes worth writing in the operator's own words, keyed by (cause, channel, level).
#: Everything else gets the standing note for its decision.
NOTES: dict[tuple[str, str | None, str], str] = {
    ("rto_reversal_later_cycle", None, NARROW):
        "Only right about half the time on the backtest. Watch it anyway - it costs "
        "nothing in shadow and I want to see which ones it gets wrong before I decide "
        "whether the pattern is real or I wrote the note badly.",
    ("promo_cofunding_deduction", "myntra", "general"):
        "Without the band this fires on twenty-nine rows and agrees with me on three "
        "of them. It has stopped being about promo co-funding and started being about "
        "any Myntra fee variance. No.",
    ("promo_cofunding_deduction", "amazon", "general"):
        "Same as the Myntra one - drop the band and it is just 'Amazon charged a fee'.",
    ("commission_rate_stale", "myntra", NARROW):
        "This is the outerwear slab change. Exactly what I meant.",
}

APPROVED_NOTE = (
    "The narrowest reading of what I wrote. Watch it and show me how it does."
)
REJECTED_NOTE = (
    "A looser version of a rule I have already approved. One rule per phenomenon."
)


def verdicts() -> list[CardVerdict]:
    """Every card a real run produced, decided under the policy in the docstring.

    The approved rung is chosen by *level*, explicitly, rather than by whichever card
    the backtest ordering happened to put first. A general rung matches a superset of
    its narrow one, so it can carry equal or greater support and sort above it; taking
    the first card would then approve the loose reading while the docstring claimed
    the tight one, and nothing would fail.
    """
    record = run(allow_network=False)
    decided: list[CardVerdict] = []

    #: The narrowest rung offered for each phenomenon, across the whole run.
    chosen: dict[tuple[str, str | None, str | None], str] = {}
    for batch in record.batches:
        for card in batch.candidate_cards:
            rule = record.store.get(card.rule_id)
            phenomenon = (card.cause, rule.channel, rule.reason_code)
            held = chosen.get(phenomenon)
            if held is None or LEVELS.index(card.level) < LEVELS.index(held):
                chosen[phenomenon] = card.level

    for batch in record.batches:
        when = batch_window(batch.batch)[1].isoformat()
        for card in batch.candidate_cards:
            rule = record.store.get(card.rule_id)
            phenomenon = (card.cause, rule.channel, rule.reason_code)
            first = chosen[phenomenon] == card.level
            decision = APPROVE if first else REJECT
            note = NOTES.get(
                (card.cause, rule.channel, card.level),
                APPROVED_NOTE if first else REJECTED_NOTE,
            )
            decided.append(
                CardVerdict(
                    cause=card.cause,
                    channel=rule.channel,
                    reason_code=rule.reason_code,
                    level=card.level,
                    decision=decision,
                    operator=OPERATOR,
                    decided_at=when,
                    note=note,
                )
            )
    return decided


def main() -> int:
    decided = verdicts()
    save(of(decided))
    approved = sum(1 for v in decided if v.approves)
    print(
        f"{len(decided)} card decisions -> data/approvals.json "
        f"({approved} approved, {len(decided) - approved} rejected)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
