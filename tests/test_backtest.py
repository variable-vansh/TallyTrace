"""Scoring a candidate on history, and the gate that stands on the score.

The backtest is the difference between a system that induces rules and one that
induces rules it can defend, so these tests are mostly about the ways a number here
could flatter a rule that has not earned anything.
"""

from __future__ import annotations

from decimal import Decimal

from pipeline.cases import CaseFeatures
from pipeline.rules.backtest import (
    Demonstration,
    ScoredCandidate,
    backtest,
    survivors,
)
from pipeline.rules.models import Rule, RuleState

D = Decimal


def features(channel="myntra", reason="fee_variance_outside_tolerance", pct=D("8.0")):
    return CaseFeatures(
        channel=channel, reason=reason, bucket="variance", transaction_type="payment",
        direction="short", variance_inr=D("120.00"), fee_variance_pct=pct,
        net_variance_pct=None, days_after_settlement=None, days_since_order=None,
        days_late=None,
    )


def rule(cause="commission_rate_stale", **kwargs) -> Rule:
    defaults: dict = dict(
        rule_id="R-01", cause=cause, resolution_class="internal_fix",
        plain_words="Myntra bills a higher slab than the master rate says.",
        channel="myntra", reason_code="fee_variance_outside_tolerance",
        action_type="update_ledger_rate",
    )
    return Rule(**{**defaults, **kwargs})


def demo(index: int, cause="commission_rate_stale", **kwargs) -> Demonstration:
    return Demonstration(
        resolution_id=f"res_{index:04d}", case_id=f"case-0{index}-ord_{index}",
        batch=1, features=features(**kwargs), cause=cause,
    )


# --------------------------------------------------------------------------- #
# Coverage, precision, support
# --------------------------------------------------------------------------- #


def test_a_candidate_that_fires_on_nothing_scores_nothing() -> None:
    score = backtest(rule(channel="amazon"), [demo(1), demo(2)])
    assert score.coverage == 0
    assert score.support == 0
    assert score.precision is None


def test_precision_is_agreement_with_the_human_on_the_rows_it_fired_on() -> None:
    history = [demo(1), demo(2), demo(3, cause="fee_mismatch_other")]
    score = backtest(rule(), history)
    assert score.coverage == 3
    assert score.agreements == 2
    assert score.precision == D("0.6667")


def test_support_counts_distinct_demonstrations_and_not_rows() -> None:
    """The whole point of the gate. One sentence resolving eighty rows is one
    piece of evidence, and counting rows would let it look like eighty."""
    one_sentence = [
        Demonstration(resolution_id="res_0001", case_id=f"case-01-ord_{n}", batch=1,
                      features=features(), cause="commission_rate_stale")
        for n in range(80)
    ]
    score = backtest(rule(), one_sentence)
    assert score.coverage == 80
    assert score.support == 1


def test_a_disagreeing_case_covers_but_does_not_support() -> None:
    """Firing on a row the human resolved differently is not evidence *for* a rule."""
    score = backtest(rule(), [demo(1, cause="fee_mismatch_other")])
    assert score.coverage == 1
    assert score.support == 0
    assert score.precision == D("0.0000")


def test_the_rows_it_would_have_acted_on_are_named_not_counted() -> None:
    """A number an operator cannot open is not evidence."""
    score = backtest(rule(), [demo(1), demo(2)])
    assert score.fired_on == ("case-01-ord_1", "case-02-ord_2")
    assert score.supporting_resolution_ids == ("res_0001", "res_0002")


# --------------------------------------------------------------------------- #
# Conflicts
# --------------------------------------------------------------------------- #


def test_an_active_rule_wanting_something_else_done_is_a_conflict() -> None:
    active = rule(rule_id="R-99", state=RuleState.ACTIVE, action_type="write_off_variance")
    score = backtest(rule(), [demo(1)], [active])
    assert score.conflicts == 1


def test_an_active_rule_that_agrees_about_the_action_is_not_a_conflict() -> None:
    active = rule(rule_id="R-99", state=RuleState.ACTIVE, action_type="update_ledger_rate")
    assert backtest(rule(), [demo(1)], [active]).conflicts == 0


def test_an_active_rule_that_does_not_fire_is_not_a_conflict() -> None:
    elsewhere = rule(rule_id="R-99", channel="amazon", state=RuleState.ACTIVE,
                     action_type="write_off_variance")
    assert backtest(rule(), [demo(1)], [elsewhere]).conflicts == 0


# --------------------------------------------------------------------------- #
# The support threshold
# --------------------------------------------------------------------------- #


def scored(support: int, level="narrow", **kwargs) -> ScoredCandidate:
    history = [demo(n) for n in range(1, support + 1)] or [demo(1, cause="other_cause")]
    return ScoredCandidate(level=level, rule=rule(**kwargs), score=backtest(rule(**kwargs), history))


def test_a_candidate_below_the_threshold_is_discarded_and_kept_for_the_record() -> None:
    kept, discarded = survivors([scored(1)], min_support=2)
    assert kept == []
    assert len(discarded) == 1


def test_a_candidate_at_the_threshold_survives() -> None:
    kept, discarded = survivors([scored(2)], min_support=2)
    assert len(kept) == 1
    assert discarded == []


def test_survivors_are_ordered_deterministically_without_reading_a_rule_id() -> None:
    """Candidates are scored before any id is handed out, so an id-based tiebreak
    would order them by an identifier that does not exist yet."""
    candidates = [scored(2), scored(4), scored(3)]
    kept, _ = survivors(candidates, min_support=2)
    assert [c.score.support for c in kept] == [4, 3, 2]
    assert survivors(candidates, min_support=2)[0] == kept


def test_nothing_in_the_backtest_writes_to_the_rule_it_scored() -> None:
    """It scores; it does not decide, and it does not mutate."""
    candidate = rule()
    before = candidate.to_json()
    backtest(candidate, [demo(1), demo(2)])
    assert candidate.to_json() == before
