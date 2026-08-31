"""Batch proposal cards -- the user-facing shape of everything else in this package.

When a rule matches fourteen rows in a new batch, the operator should see one card,
not fourteen exceptions:

    Myntra commission billing at 27.2%, your master rate says 25%.
    Explains 14 rows, ₹8,340.
    Learned from your resolution on 12 January.
    [ Accept all ]  [ Review individually ]  [ Not this time ]

Cards are built from decisions, not from rules, so a card can only ever describe rows
the rule actually matched this batch. Two kinds exist and the difference is stated on
the card:

- a rule that **fired** -- the rows are already resolved and the card says so;
- a rule that matched and was **held by a guardrail** -- the rows are not resolved,
  and the card collapses fourteen four-figure reversals into one decision instead of
  fourteen. That is a smaller claim than automation and it is an honest one.

"Not this time" is not a dismissal. It records a negative observation against the
rule, which moves its live precision and can retire it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pipeline.rules.apply import AUTO_RESOLVED, HELD, SHADOWED, Decision
from pipeline.rules.models import Rule

ZERO = Decimal("0.00")

#: Outcomes worth collapsing into a card. A case no rule matched has nothing to
#: collapse and belongs in the queue as itself.
CARDABLE = (AUTO_RESOLVED, HELD, SHADOWED)


@dataclass(frozen=True)
class Proposal:
    """One rule's effect on one batch, as one decision for a human."""

    batch: int
    rule_id: str
    cause: str
    plain_words: str
    outcome: str                     # auto_resolved | held_by_guardrail | shadow_prediction
    case_ids: tuple[str, ...]
    settlement_row_ids: tuple[str, ...]
    impact_inr: Decimal
    learned_from_resolution: str
    learned_from_operator: str
    learned_in_batch: int
    rule_state: str
    held_because: str

    @property
    def rows(self) -> int:
        return len(self.settlement_row_ids)

    @property
    def headline(self) -> str:
        return self.plain_words

    @property
    def subhead(self) -> str:
        return f"Explains {len(self.case_ids)} exception(s) across {self.rows} row(s), ₹{self.impact_inr}."

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "rule_id": self.rule_id,
            "cause": self.cause,
            "headline": self.headline,
            "subhead": self.subhead,
            "outcome": self.outcome,
            "rule_state": self.rule_state,
            "cases": len(self.case_ids),
            "case_ids": list(self.case_ids),
            "rows": self.rows,
            "settlement_row_ids": list(self.settlement_row_ids),
            "impact_inr": str(self.impact_inr),
            "learned_from": {
                "resolution_id": self.learned_from_resolution,
                "operator": self.learned_from_operator,
                "batch": self.learned_in_batch,
            },
            "held_because": self.held_because,
        }


def build(batch: int, decisions: list[Decision], rules: dict[str, Rule]) -> list[Proposal]:
    """Collapse a batch's decisions into one card per (rule, outcome).

    Split by outcome as well as by rule on purpose: a rule that resolved nine small
    variances and was held on two large ones has done two different things, and one
    card claiming eleven would overstate the first and hide the second.
    """
    grouped: dict[tuple[str, str], list[Decision]] = {}
    for decision in decisions:
        rule_id = decision.provenance.rule_id
        if rule_id is None or decision.provenance.outcome not in CARDABLE:
            continue
        grouped.setdefault((rule_id, decision.provenance.outcome), []).append(decision)

    proposals: list[Proposal] = []
    for (rule_id, outcome), members in sorted(grouped.items()):
        rule = rules[rule_id]
        held = next(
            (d.guardrails.reason for d in members if d.guardrails and d.guardrails.held), ""
        )
        proposals.append(
            Proposal(
                batch=batch,
                rule_id=rule_id,
                cause=rule.cause,
                plain_words=rule.plain_words,
                outcome=outcome,
                case_ids=tuple(sorted(d.case.case_id for d in members)),
                settlement_row_ids=tuple(
                    sorted(row for d in members for row in d.case.settlement_row_ids)
                ),
                impact_inr=sum((d.impact_inr for d in members), ZERO),
                learned_from_resolution=rule.source_resolution_id,
                learned_from_operator=rule.source_operator,
                learned_in_batch=rule.created_batch,
                rule_state=rule.state.value,
                held_because=held,
            )
        )
    return proposals
