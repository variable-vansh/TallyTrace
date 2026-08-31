"""Guardrails, and the ordering that is the entire point of them.

A rule's confidence is an opinion about a pattern. A threshold is a decision about
risk. The opinion never wins, and the way that is true in the code rather than in the
README is that the guardrail result is computed from the rule that has *already*
matched and can only take the decision away from it.

Every number here comes from ``config/thresholds.yaml``. The tests read the real file
rather than a fixture, so a threshold changed in config and not in code fails here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline.cases import CaseFeatures, ExceptionCase
from pipeline.config import thresholds
from pipeline.matcher import Bucket, Reason, Verdict
from pipeline.rules.apply import AUTO_RESOLVED, HELD, SHADOWED, UNMATCHED, decide
from pipeline.rules.guardrails import ALWAYS_HUMAN_CLASSES, evaluate, guardrail_config_from
from pipeline.rules.models import Rule, RuleState

D = Decimal
CFG = guardrail_config_from(thresholds())


def features(**kwargs) -> CaseFeatures:
    defaults = dict(
        channel="myntra", reason="fee_variance_outside_tolerance", bucket="variance",
        transaction_type=None, direction="short", variance_inr=D("32.73"),
        fee_variance_pct=D("8.80"), net_variance_pct=D("-3.74"),
        days_after_settlement=None, days_since_order=None, days_late=None,
    )
    return CaseFeatures(**{**defaults, **kwargs})


def case(feat: CaseFeatures) -> ExceptionCase:
    verdict = Verdict(
        table="internal_ledger", row_id="ord_1", bucket=Bucket.VARIANCE,
        reason=Reason.FEE_OUTSIDE_TOLERANCE, order_id="ord_1", channel=feat.channel,
        impact_inr=feat.variance_inr,
    )
    return ExceptionCase(
        case_id="case-01-ord_1", batch=1, kind="order", key="ord_1",
        verdicts=(verdict,), features=feat, impact_inr=feat.variance_inr,
    )


def rule(**kwargs) -> Rule:
    defaults = dict(
        rule_id="R-01", cause="commission_rate_stale", resolution_class="internal_fix",
        plain_words="Myntra bills 27.2% against a 25% master rate.",
        channel="myntra", reason_code="fee_variance_outside_tolerance",
        variance_band_pct=(D("8.0"), D("9.5")), direction="short",
        state=RuleState.ACTIVE,
    )
    return Rule(**{**defaults, **kwargs})


# --------------------------------------------------------------------------- #
# The three guardrails
# --------------------------------------------------------------------------- #


def test_the_ceiling_comes_from_config_and_holds_above_it() -> None:
    ceiling = CFG.max_variance_inr
    assert evaluate(rule(), features(variance_inr=ceiling), CFG).held is False
    result = evaluate(rule(), features(variance_inr=ceiling + D("0.01")), CFG)
    assert result.held
    assert [c.name for c in result.held_by] == ["max_variance_inr"]


@pytest.mark.parametrize("cause", sorted(CFG.never_auto_resolve_causes))
def test_a_blocked_cause_is_blocked_however_confident_the_rule(cause: str) -> None:
    blocked = rule(cause=cause, resolution_class="internal_fix")
    result = evaluate(blocked, features(variance_inr=D("1.00")), CFG)
    assert result.held
    assert "never_auto_resolve" in [c.name for c in result.held_by]


@pytest.mark.parametrize("klass", sorted(ALWAYS_HUMAN_CLASSES))
def test_tax_review_investigate_and_claims_are_always_human(klass: str) -> None:
    result = evaluate(rule(resolution_class=klass), features(variance_inr=D("1.00")), CFG)
    assert result.held
    assert "resolution_class" in [c.name for c in result.held_by]


def test_every_guardrail_is_evaluated_and_recorded_even_when_one_already_held() -> None:
    """A short circuit would lose the record. 'Which guardrails did you check?' is a
    question asked about the resolutions that went through, not only the held ones."""
    result = evaluate(
        rule(cause="chargeback_deduction", resolution_class="counterparty_claim"),
        features(variance_inr=D("50000.00")),
        CFG,
    )
    assert len(result.checks) == 3
    assert len(result.held_by) == 3
    assert result.rendered == (
        "max_variance_inr:hold", "never_auto_resolve:hold", "resolution_class:hold",
    )


def test_a_passing_evaluation_still_records_all_three() -> None:
    result = evaluate(rule(), features(), CFG)
    assert result.rendered == (
        "max_variance_inr:pass", "never_auto_resolve:pass", "resolution_class:pass",
    )
    assert not result.held


# --------------------------------------------------------------------------- #
# Ordering: rule first, guardrail second, guardrail wins
# --------------------------------------------------------------------------- #


def test_an_active_rule_that_matches_is_overruled_by_a_guardrail() -> None:
    """The whole design in one assertion. The rule matched; it does not fire."""
    over_ceiling = features(variance_inr=CFG.max_variance_inr + D("1.00"))
    decision, observation = decide(case(over_ceiling), [rule()], CFG)

    assert decision.provenance.rule_id == "R-01"       # the rule did match
    assert decision.provenance.outcome == HELD         # and it still did not fire
    assert decision.needs_human
    assert "ceiling" in decision.provenance.note
    # The observation is still recorded: a rule that was right and blocked has
    # earned the credit for being right.
    assert observation is not None


def test_a_shadow_rule_predicts_and_logs_and_the_human_still_sees_it() -> None:
    decision, observation = decide(case(features()), [rule(state=RuleState.SHADOW)], CFG)
    assert decision.provenance.outcome == SHADOWED
    assert decision.needs_human
    assert decision.provenance.guardrails_evaluated == ()   # never reached
    assert observation.state_at_prediction == "shadow"


def test_an_active_rule_inside_every_threshold_resolves_and_says_so() -> None:
    decision, observation = decide(case(features()), [rule()], CFG)
    assert decision.provenance.outcome == AUTO_RESOLVED
    assert decision.resolved
    assert decision.provenance.rule_state_at_fire == "active"
    assert len(decision.provenance.guardrails_evaluated) == 3
    assert observation.predicted_cause == "commission_rate_stale"


def test_a_case_no_rule_matched_is_untouched_and_earns_nobody_an_observation() -> None:
    decision, observation = decide(case(features(channel="offline")), [rule()], CFG)
    assert decision.provenance.outcome == UNMATCHED
    assert decision.provenance.rule_id is None
    assert observation is None


def test_provenance_is_complete_on_every_auto_resolution() -> None:
    decision, _ = decide(
        case(features()),
        [rule(source_resolution_id="res_0001", source_operator="priya.n@demostore.in")],
        CFG,
    )
    p = decision.provenance
    assert p.case_id and p.rule_id and p.rule_state_at_fire
    assert p.source_resolution_id == "res_0001"
    assert p.source_operator == "priya.n@demostore.in"
    assert p.proposed_cause == "commission_rate_stale"
    assert len(p.guardrails_evaluated) == 3
