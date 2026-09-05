"""Rule application, and the provenance every auto-resolution leaves behind.

The order of operations is the design:

1. select the most specific matching rule (``predicates.select``);
2. if it is not ``active``, log a shadow prediction and stop -- the human still works
   the case;
3. run every guardrail (``guardrails.evaluate``);
4. auto-resolve only if all of them passed.

Step 3 after step 1, always. A rule cannot out-confidence a threshold, and the way
you know that is true in the code rather than in the README is that the guardrail
result is computed from the rule that already won and can only take the decision
away from it.

Every outcome -- resolved, held, shadowed, unmatched -- carries a
:class:`Provenance` record. Clicking a transaction in the UI shows that record: what
matched, what varied, which rule fired, whose resolution it descended from, and which
guardrails were evaluated. That one screen answers "would you trust it" better than
any paragraph.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pipeline.cases import ExceptionCase
from pipeline.rules.guardrails import GuardrailConfig, GuardrailResult, evaluate
from pipeline.rules.models import Observation, Rule, RuleState
from pipeline.rules.predicates import Selection, select, specificity

#: What happened to a case this batch.
AUTO_RESOLVED = "auto_resolved"
HELD = "held_by_guardrail"
SHADOWED = "shadow_prediction"
CONFLICTED = "rules_disagree"
UNMATCHED = "no_rule_matched"


@dataclass(frozen=True)
class Provenance:
    """The full decision path for one case. Serialisable, and shown verbatim in the UI."""

    case_id: str
    batch: int
    outcome: str
    rule_id: str | None
    #: The version of the rule that fired. An id alone cannot answer "what did this
    #: rule say when it closed that row?" once the rule has been edited since.
    rule_version: int | None
    rule_state_at_fire: str | None
    source_resolution_id: str | None
    source_operator: str | None
    proposed_cause: str | None
    guardrails_evaluated: tuple[str, ...]
    guardrail_detail: tuple[str, ...]
    note: str

    def to_json(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "batch": self.batch,
            "outcome": self.outcome,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "rule_state_at_fire": self.rule_state_at_fire,
            "source_resolution_id": self.source_resolution_id,
            "source_operator": self.source_operator,
            "proposed_cause": self.proposed_cause,
            "guardrails_evaluated": list(self.guardrails_evaluated),
            "guardrail_detail": list(self.guardrail_detail),
            "note": self.note,
        }


@dataclass(frozen=True)
class Decision:
    """One case's outcome plus the provenance behind it."""

    case: ExceptionCase
    provenance: Provenance
    rule: Rule | None
    guardrails: GuardrailResult | None

    @property
    def resolved(self) -> bool:
        return self.provenance.outcome == AUTO_RESOLVED

    @property
    def needs_human(self) -> bool:
        return not self.resolved

    @property
    def impact_inr(self) -> Decimal:
        return self.case.impact_inr


def _provenance(
    case: ExceptionCase,
    outcome: str,
    note: str,
    rule: Rule | None = None,
    guardrails: GuardrailResult | None = None,
) -> Provenance:
    return Provenance(
        case_id=case.case_id,
        batch=case.batch,
        outcome=outcome,
        rule_id=None if rule is None else rule.rule_id,
        rule_version=None if rule is None else rule.version,
        rule_state_at_fire=None if rule is None else rule.state.value,
        source_resolution_id=None if rule is None else rule.source_resolution_id,
        source_operator=None if rule is None else rule.source_operator,
        proposed_cause=None if rule is None else rule.cause,
        guardrails_evaluated=() if guardrails is None else guardrails.rendered,
        guardrail_detail=() if guardrails is None else tuple(c.detail for c in guardrails.checks),
        note=note,
    )


def decide(
    case: ExceptionCase, rules: list[Rule], cfg: GuardrailConfig
) -> tuple[Decision, Observation | None]:
    """What to do with one case, and the observation the winning rule earns.

    The observation is returned rather than written, because a rule is immutable and
    the caller owns the store. Shadow rules produce observations too -- that is the
    entire purpose of shadow mode.
    """
    selection: Selection = select([r for r in rules if r.enabled], case.features)

    if selection.conflict:
        return (
            Decision(case, _provenance(case, CONFLICTED, selection.reason), None, None),
            None,
        )
    rule = selection.winner
    if rule is None:
        return Decision(case, _provenance(case, UNMATCHED, selection.reason), None, None), None

    observation = Observation(
        batch=case.batch,
        case_id=case.case_id,
        predicted_cause=rule.cause,
        state_at_prediction=rule.state.value,
    )

    if rule.state is not RuleState.ACTIVE:
        note = (
            f"{rule.rule_id} predicts {rule.cause} but is in {rule.state.value}; "
            "the exception still goes to a human"
        )
        return Decision(case, _provenance(case, SHADOWED, note, rule), rule, None), observation

    guardrails = evaluate(rule, case.features, cfg)
    if guardrails.held:
        note = f"{rule.rule_id} matched at specificity {specificity(rule)}; {guardrails.reason}"
        return (
            Decision(case, _provenance(case, HELD, note, rule, guardrails), rule, guardrails),
            observation,
        )

    note = (
        f"{rule.rule_id} matched at specificity {specificity(rule)}; "
        f"all {len(guardrails.checks)} guardrails passed"
    )
    return (
        Decision(case, _provenance(case, AUTO_RESOLVED, note, rule, guardrails), rule, guardrails),
        observation,
    )
