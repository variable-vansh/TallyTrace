"""The checkpoint's done conditions, asserted over a real run.

Every number here comes from ``make score`` over the shipped corpus. Where a
condition is not met, the test says so in its name and its message rather than being
softened until it passes — the harness exists to catch what went wrong, and a test
suite that only encodes the good news is the same failure one layer up.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from harness.learning import cause_by_key, true_cause_of
from pipeline.config import generation, thresholds
from pipeline.rules.apply import AUTO_RESOLVED
from pipeline.rules.guardrails import guardrail_config_from
from pipeline.rules.models import RuleState

D = Decimal


# --------------------------------------------------------------------------- #
# Precision holds
# --------------------------------------------------------------------------- #


def test_auto_resolution_precision_holds_above_ninety_five_percent(scored) -> None:
    """The decline is worthless without this. Watch precision, not just the curve."""
    precision = scored.learning.overall_precision
    assert precision is not None, "nothing auto-resolved, so there is nothing to trust"
    assert precision >= D("95"), f"auto-resolution precision fell to {precision}%"


def test_precision_holds_in_every_batch_that_resolved_anything(scored) -> None:
    weak = [
        (b.batch, b.auto_resolution_precision)
        for b in scored.learning.batches
        if b.auto_resolution_precision is not None and b.auto_resolution_precision < D("85")
    ]
    assert weak == [], f"precision dipped: {weak}"


def test_precision_does_not_decay_as_volume_grows(scored) -> None:
    """A rule set that holds on 6 resolutions and slips on 35 has memorised a batch."""
    early = [b for b in scored.learning.batches if b.batch <= 5 and b.scored_auto_resolutions]
    late = [b for b in scored.learning.batches if b.batch > 5 and b.scored_auto_resolutions]
    correct_late = sum(b.correct_auto_resolutions for b in late)
    scored_late = sum(b.scored_auto_resolutions for b in late)
    assert scored_late > sum(b.scored_auto_resolutions for b in early)
    assert correct_late / scored_late >= 0.95


# --------------------------------------------------------------------------- #
# The curve
# --------------------------------------------------------------------------- #


def test_learned_rules_move_the_review_rate_and_the_matcher_does_not(scored) -> None:
    """Two series on purpose. The matcher's own rate must not move — if it does, a
    tolerance was widened and the decline came from somewhere other than learning."""
    for metric in scored.metrics:
        assert metric.net_review_rate <= metric.review_rate
    assert any(m.net_review_rate < m.review_rate for m in scored.metrics)


def test_the_human_decision_rate_declines_across_the_ten_batches(scored) -> None:
    """The series the batch-proposal design is about: how many separate decisions a
    human has to make, as a percentage of the batch."""
    from harness.metrics import pct

    series = [
        pct(learned.human_touchpoints, metric.settlement_rows)
        for learned, metric in zip(scored.learning.batches, scored.metrics)
    ]
    assert series[-1] < series[0], f"touchpoint rate did not decline: {series}"
    assert series[-1] < series[0] / 2, f"decline was marginal: {series}"


def test_the_curve_plateaus_above_zero(scored) -> None:
    """A curve to zero reads as scripted, and would mean a guardrail is not firing."""
    for learned, metric in zip(scored.learning.batches, scored.metrics):
        assert learned.human_touchpoints > 0, f"batch {learned.batch} needed nobody"
        assert metric.net_review_rate > D("0")


def test_the_share_of_the_queue_the_system_handles_grows(scored) -> None:
    """The claim the review rate is really about, stated as a share rather than a
    count, so a growing corpus cannot flatter it."""
    late = scored.learning.batches[-1]
    assert late.auto_resolved_cases + late.held_by_guardrail_cases >= late.queue_cases / 2


# --------------------------------------------------------------------------- #
# Abstention
# --------------------------------------------------------------------------- #


def test_the_held_out_causes_are_not_auto_resolved_on_first_sight(scored) -> None:
    """Correct abstention is the hardest behaviour to fake and the easiest to show."""
    assert len(scored.learning.abstentions) == len(generation()["held_out"])
    for entry in scored.learning.abstentions:
        assert entry.first_batch == generation()["held_out"][entry.cause]
        assert entry.cases_on_first_sight > 0, f"{entry.cause} never appeared"
        assert entry.auto_resolved_on_first_sight == 0, (
            f"{entry.cause} was automated the first time it was ever seen"
        )


def test_the_held_out_causes_are_never_auto_resolved_at_all(scored) -> None:
    """Both are counterparty claims, and a claim is never a resolution: closing a row
    someone else owes money on is a write-off nobody authorised."""
    for entry in scored.learning.abstentions:
        assert entry.auto_resolved_ever == 0, entry.cause


def test_no_cause_on_the_never_auto_resolve_list_was_ever_auto_resolved(scored) -> None:
    blocked = set(thresholds()["auto_resolution"]["never_auto_resolve_causes"])
    lookup = cause_by_key(scored.key)
    offenders = [
        (b.batch, d.case.case_id, true_cause_of(d.case.row_keys, lookup))
        for b in scored.run.batches
        for d in b.auto_resolved
        if true_cause_of(d.case.row_keys, lookup) in blocked
    ]
    assert offenders == [], offenders


def test_the_rupee_ceiling_was_never_crossed(scored) -> None:
    ceiling = guardrail_config_from(thresholds()).max_variance_inr
    over = [
        (d.case.case_id, d.case.features.variance_inr)
        for b in scored.run.batches
        for d in b.auto_resolved
        if d.case.features.variance_inr > ceiling
    ]
    assert over == [], f"auto-resolved above the ₹{ceiling} ceiling: {over}"


def test_the_system_automates_volume_and_escalates_value(scored) -> None:
    """A modest rupee share with high precision is the better result and the honest
    one. If this ever inverts, a guardrail has been loosened."""
    auto = sum(b.rupees_auto_resolved for b in scored.learning.batches)
    escalated = sum(b.rupees_escalated for b in scored.learning.batches)
    assert auto < escalated
    assert sum(b.auto_resolved_cases for b in scored.learning.batches) > 100


# --------------------------------------------------------------------------- #
# The lifecycle actually ran
# --------------------------------------------------------------------------- #


def test_at_least_one_rule_retired_itself_and_says_why(scored) -> None:
    """Retirement is not a failure to hide. It is the evidence the lifecycle works."""
    retired = [r for r in scored.run.store.rules if r.state is RuleState.RETIRED]
    assert retired, "no rule retired: the lifecycle has not been tested by the data"
    for rule in retired:
        floor = thresholds()["rule_lifecycle"]["retirement_precision_floor"]
        assert rule.precision < Decimal(floor)
        assert len(rule.judged) >= int(thresholds()["rule_lifecycle"]["retirement_min_observations"])
        assert "below the" in rule.transitions[-1].reason


def test_no_rule_fired_before_it_was_promoted(scored) -> None:
    """Shadow mode is not skippable. Every auto-resolution names the state the rule
    was in when it fired, and it is always active."""
    states = {
        d.provenance.rule_state_at_fire
        for b in scored.run.batches
        for d in b.auto_resolved
    }
    assert states == {"active"}


def test_every_active_rule_spent_time_in_shadow_first(scored) -> None:
    for rule in scored.run.store.rules:
        if rule.state is not RuleState.ACTIVE:
            continue
        path = [t.to_state for t in rule.transitions]
        assert path[:2] == ["shadow", "active"], (rule.rule_id, path)
        assert path.index("shadow") < path.index("active")


def test_promotion_never_happened_in_the_batch_the_rule_was_born(scored) -> None:
    """The lag between learning and automating is what makes the decline believable."""
    for rule in scored.run.store.rules:
        promotions = [t for t in rule.transitions if t.to_state == "active"]
        for transition in promotions:
            assert transition.batch > rule.created_batch, rule.rule_id


# --------------------------------------------------------------------------- #
# The near-miss
# --------------------------------------------------------------------------- #


def test_the_near_miss_shows_up_as_a_real_miss_rather_than_passing_silently(scored) -> None:
    """The most valuable rows in the dataset. A stale-rate rule fires on them and is
    wrong, and that false positive has to be visible in the precision number."""
    lookup = cause_by_key(scored.key)
    misses = [
        (b.batch, d.case.case_id, d.provenance.proposed_cause,
         true_cause_of(d.case.row_keys, lookup))
        for b in scored.run.batches
        for d in b.auto_resolved
        if true_cause_of(d.case.row_keys, lookup) not in (None, d.provenance.proposed_cause)
    ]
    assert misses, "no false positive at all — check the near-miss survived generation"
    assert scored.learning.overall_precision < D("100"), (
        "precision is a clean 100% while a known false positive exists, so something "
        "is scoring the misses away"
    )
    assert all(claimed == "commission_rate_stale" for _, _, claimed, _ in misses)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def test_every_auto_resolution_carries_a_complete_provenance_chain(scored) -> None:
    """Clicking any transaction has to answer the whole question, not most of it."""
    for batch in scored.run.batches:
        for decision in batch.auto_resolved:
            p = decision.provenance
            assert p.outcome == AUTO_RESOLVED
            assert p.rule_id and p.rule_state_at_fire == "active"
            assert p.source_resolution_id, p.case_id
            assert p.source_operator, p.case_id
            assert p.proposed_cause
            assert len(p.guardrails_evaluated) == 3
            assert all(check.endswith(":pass") for check in p.guardrails_evaluated)
            assert p.note


def test_every_rule_traces_back_to_a_resolution_a_person_wrote(scored) -> None:
    from pipeline.rules import resolutions as operator_log

    by_id = {r.resolution_id: r for r in operator_log.load().resolutions}
    for rule in scored.run.store.rules:
        assert rule.source_resolution_id in by_id, rule.rule_id
        assert by_id[rule.source_resolution_id].text.strip()


def test_every_queued_case_that_the_matcher_could_read_got_a_hypothesis(scored) -> None:
    for batch in scored.run.batches:
        for case in batch.cases:
            if case.features.bucket == "quarantined":
                assert case.case_id not in batch.hypotheses
            else:
                assert case.case_id in batch.hypotheses, case.case_id


def test_every_hypothesis_names_a_cause_from_the_frozen_enum(scored) -> None:
    from pipeline.models import Cause

    for batch in scored.run.batches:
        for hypothesis in batch.hypotheses.values():
            assert isinstance(hypothesis.cause, Cause)
            assert D("0") <= hypothesis.confidence <= D("1")


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #


def test_the_model_costs_something_and_the_harness_reports_it(scored) -> None:
    """Checkpoint 2 wired this at zero on purpose. It must now be non-zero, or the
    plumbing is still untested by the thing it was built for."""
    assert scored.run.ledger.total().total_tokens > 0
    assert sum(m.cost_inr for m in scored.metrics) > 0
    assert all(m.cost_per_transaction_inr >= 0 for m in scored.metrics)
