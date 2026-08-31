"""Rule matching. Deterministic predicate evaluation, and nothing else.

No model is imported here and none ever will be. Induction is language work; asking
whether a rule's conditions hold of a row is a handful of comparisons, and a
probabilistic answer to that question is not a feature, it is a liability -- it books
a resolution against a row nobody chose, and the audit trail reads "the rule was
fairly sure".

Precedence is by **specificity**: a rule that constrains four things beats one that
constrains two, because the narrower rule is the one that was written about this
exact phenomenon. Two rules of equal specificity that disagree about the cause is
not a tie to be broken -- it is a case the system does not understand, and it goes
to a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pipeline.cases import CaseFeatures
from pipeline.rules.models import Rule


def _within(value: Decimal | None, band: tuple[Decimal, Decimal] | None) -> bool:
    """Inclusive band test. A rule that names a band and a row with no such number
    is a miss, not a pass: silence is not evidence."""
    if band is None:
        return True
    if value is None:
        return False
    return band[0] <= value <= band[1]


def _within_days(value: int | None, band: tuple[int, int] | None) -> bool:
    if band is None:
        return True
    if value is None:
        return False
    return band[0] <= value <= band[1]


def matches(rule: Rule, features: CaseFeatures) -> bool:
    """Do this rule's conditions hold of this case?"""
    if rule.channel is not None and rule.channel != features.channel:
        return False
    if rule.reason_code is not None and rule.reason_code != features.reason:
        return False
    if rule.transaction_type is not None and rule.transaction_type != features.transaction_type:
        return False
    if rule.direction != "any" and rule.direction != features.direction:
        return False
    if not _within(features.fee_variance_pct, rule.variance_band_pct):
        return False
    if not _within(features.net_variance_pct, rule.net_variance_band_pct):
        return False
    if not _within_days(features.days_after_settlement, rule.lag_window_days):
        return False
    return True


def specificity(rule: Rule) -> int:
    """How many things the rule constrains. More is narrower is higher precedence."""
    return sum(
        1
        for constraint in (
            rule.channel,
            rule.reason_code,
            rule.transaction_type,
            rule.variance_band_pct,
            rule.net_variance_band_pct,
            rule.lag_window_days,
            None if rule.direction == "any" else rule.direction,
        )
        if constraint is not None
    )


@dataclass(frozen=True)
class Selection:
    """Which rule wins on a case, and why the runners-up did not."""

    winner: Rule | None
    contenders: tuple[Rule, ...]
    conflict: bool          # equally specific rules disagreeing about the cause

    @property
    def reason(self) -> str:
        if self.conflict:
            causes = sorted({rule.cause for rule in self.contenders})
            return f"equally specific rules disagree ({', '.join(causes)}); sent to a human"
        if self.winner is None:
            return "no rule matched"
        return f"{self.winner.rule_id} matched at specificity {specificity(self.winner)}"


def select(rules: list[Rule], features: CaseFeatures) -> Selection:
    """Most specific match wins; an equally specific disagreement goes to a human."""
    hits = [rule for rule in rules if matches(rule, features)]
    if not hits:
        return Selection(winner=None, contenders=(), conflict=False)

    best = max(specificity(rule) for rule in hits)
    top = sorted(
        (rule for rule in hits if specificity(rule) == best), key=lambda rule: rule.rule_id
    )
    if len({rule.cause for rule in top}) > 1:
        return Selection(winner=None, contenders=tuple(top), conflict=True)
    return Selection(winner=top[0], contenders=tuple(hits), conflict=False)
