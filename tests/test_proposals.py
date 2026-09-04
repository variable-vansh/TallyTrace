"""Batch proposal cards — one decision instead of N exceptions.

The user-facing shape of the whole learning loop, and the reason the two review
series in the report diverge. If a card can claim rows a rule did not match, or can
merge an auto-resolution with a guardrail hold, then the number of decisions a human
makes stops being a measurement and starts being a story.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline.cases import CaseFeatures, ExceptionCase
from pipeline.config import thresholds
from pipeline.matcher import Bucket, Reason, Verdict
from pipeline.rules.apply import AUTO_RESOLVED, HELD, SHADOWED, decide
from pipeline.rules.guardrails import guardrail_config_from
from pipeline.rules.models import Rule, RuleState
from pipeline.rules.proposals import build

D = Decimal
CFG = guardrail_config_from(thresholds())


def features(variance: str = "32.73", **kwargs) -> CaseFeatures:
    defaults = dict(
        channel="myntra", reason="fee_variance_outside_tolerance", bucket="variance",
        transaction_type=None, direction="short", variance_inr=D(variance),
        fee_variance_pct=D("8.80"), net_variance_pct=D("-3.74"),
        days_after_settlement=None, days_since_order=None, days_late=None,
    )
    return CaseFeatures(**{**defaults, **kwargs})


def case(index: int, feat: CaseFeatures, rows: int = 1) -> ExceptionCase:
    order = f"ord_{index:06d}"
    verdicts = tuple(
        Verdict(
            table="settlement_report", row_id=f"st_{index:03d}{n}", bucket=Bucket.VARIANCE,
            reason=Reason.FEE_OUTSIDE_TOLERANCE, order_id=order, channel=feat.channel,
        )
        for n in range(rows)
    ) + (
        Verdict(
            table="internal_ledger", row_id=order, bucket=Bucket.VARIANCE,
            reason=Reason.FEE_OUTSIDE_TOLERANCE, order_id=order, channel=feat.channel,
            impact_inr=feat.variance_inr,
        ),
    )
    return ExceptionCase(
        case_id=f"case-04-{order}", batch=4, kind="order", key=order,
        verdicts=verdicts, features=feat, impact_inr=feat.variance_inr,
    )


def rule(**kwargs) -> Rule:
    defaults = dict(
        rule_id="R-05", cause="commission_rate_stale", resolution_class="internal_fix",
        plain_words="Myntra bills commission at 27.2% while the master rate says 25%.",
        channel="myntra", reason_code="fee_variance_outside_tolerance",
        variance_band_pct=(D("8.0"), D("9.5")), direction="short", state=RuleState.ACTIVE,
        source_resolution_id="res_0006", source_operator="priya.n@demostore.in",
        created_batch=1,
    )
    return Rule(**{**defaults, **kwargs})


def cards(cases: list[ExceptionCase], rules: list[Rule]):
    decisions = [decide(c, rules, CFG)[0] for c in cases]
    return build(4, decisions, {r.rule_id: r for r in rules}), decisions


# --------------------------------------------------------------------------- #
# Collapsing
# --------------------------------------------------------------------------- #


def test_one_rule_matching_many_cases_produces_one_card() -> None:
    """Fourteen exceptions, one decision. That is the whole claim."""
    proposals, _ = cards([case(i, features()) for i in range(14)], [rule()])
    assert len(proposals) == 1
    card = proposals[0]
    assert card.rule_id == "R-05"
    assert len(card.case_ids) == 14
    assert card.rows == 14


def test_a_card_totals_only_the_rows_its_rule_actually_matched() -> None:
    matched = [case(i, features()) for i in range(3)]
    unmatched = [case(9, features(channel="offline"))]
    proposals, decisions = cards(matched + unmatched, [rule()])
    assert len(proposals) == 1
    assert proposals[0].rows == 3
    assert "case-04-ord_000009" not in proposals[0].case_ids
    assert decisions[-1].provenance.rule_id is None


def test_only_settlement_rows_are_counted_not_the_ledger_verdicts() -> None:
    """The review rate is quoted against the settlement report. A card claiming the
    ledger rows too would double the automation figure without resolving anything."""
    proposals, _ = cards([case(0, features(), rows=2)], [rule()])
    assert proposals[0].rows == 2
    assert all(row.startswith("st_") for row in proposals[0].settlement_row_ids)


def test_the_card_carries_the_money_and_the_human_it_came_from() -> None:
    proposals, _ = cards([case(i, features("100.00")) for i in range(3)], [rule()])
    card = proposals[0]
    assert card.impact_inr == D("300.00")
    assert card.learned_from_operator == "priya.n@demostore.in"
    assert card.learned_from_resolution == "res_0006"
    assert card.learned_in_batch == 1
    assert "3 exception(s)" in card.subhead and "₹300.00" in card.subhead


# --------------------------------------------------------------------------- #
# One card must not claim two different things
# --------------------------------------------------------------------------- #


def test_resolved_and_held_rows_get_separate_cards_from_the_same_rule() -> None:
    """A rule that closed nine small variances and was held on two large ones has done
    two different things. One card claiming eleven would overstate the first and hide
    the second."""
    small = [case(i, features("30.00")) for i in range(9)]
    large = [case(20 + i, features(str(CFG.default_ceiling.max_variance_inr + D("1")))) for i in range(2)]
    proposals, _ = cards(small + large, [rule()])

    by_outcome = {p.outcome: p for p in proposals}
    assert set(by_outcome) == {AUTO_RESOLVED, HELD}
    assert by_outcome[AUTO_RESOLVED].rows == 9
    assert by_outcome[HELD].rows == 2
    assert "ceiling" in by_outcome[HELD].held_because
    assert by_outcome[AUTO_RESOLVED].held_because == ""


def test_a_shadow_rules_predictions_are_carded_without_resolving_anything() -> None:
    """Shadow mode still collapses the queue for a human; it just does not close it."""
    proposals, decisions = cards(
        [case(i, features()) for i in range(4)], [rule(state=RuleState.SHADOW)]
    )
    assert [p.outcome for p in proposals] == [SHADOWED]
    assert proposals[0].rule_state == "shadow"
    assert all(d.needs_human for d in decisions)


def test_two_rules_never_share_a_card() -> None:
    myntra = [case(i, features()) for i in range(3)]
    amazon = [case(10 + i, features(channel="amazon", fee_variance_pct=D("7.50"))) for i in range(2)]
    other = rule(rule_id="R-13", channel="amazon", variance_band_pct=(D("6.8"), D("8.2")))
    proposals, _ = cards(myntra + amazon, [rule(), other])
    assert sorted(p.rule_id for p in proposals) == ["R-05", "R-13"]
    assert {p.rows for p in proposals} == {2, 3}


def test_a_case_no_rule_matched_never_appears_on_any_card() -> None:
    """It has nothing to collapse and belongs in the queue as itself."""
    proposals, _ = cards([case(0, features(channel="offline"))], [rule()])
    assert proposals == []


def test_cards_are_ordered_deterministically() -> None:
    """A card list that reshuffles between runs makes the UI diff meaningless."""
    cases = [case(i, features()) for i in range(3)]
    first, _ = cards(cases, [rule()])
    second, _ = cards(list(reversed(cases)), [rule()])
    assert [p.to_json() for p in first] == [p.to_json() for p in second]
