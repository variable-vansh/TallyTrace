"""proposed -> shadow -> active -> retired.

The lag between learning something and automating it is what makes the review-rate
decline believable. These tests hold the lag in place: a rule cannot skip shadow, it
cannot be promoted on volume alone, and a rule that stops being right demotes itself
without anyone intervening.

Thresholds come from the real ``config/thresholds.yaml``. Promotion and retirement
numbers are exactly the kind an agent shaves to make a curve steeper, so they are
read from config here rather than restated.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline.config import thresholds
from pipeline.rules.lifecycle import advance, lifecycle_config_from
from pipeline.rules.models import Observation, Rule, RuleState

D = Decimal
CFG = lifecycle_config_from(thresholds())


def rule(state: RuleState = RuleState.PROPOSED, **kwargs) -> Rule:
    defaults = dict(
        rule_id="R-01", cause="commission_rate_stale", resolution_class="internal_fix",
        plain_words="Myntra bills 27.2% against a 25% master rate.", state=state,
    )
    return Rule(**{**defaults, **kwargs})


def judged(rule_: Rule, correct: int, wrong: int) -> Rule:
    for index in range(correct + wrong):
        case_id = f"case-01-ord_{index}"
        rule_ = rule_.observing(
            Observation(batch=2, case_id=case_id, predicted_cause=rule_.cause,
                        state_at_prediction=rule_.state.value)
        ).judging(case_id, index < correct, "human_resolution")
    return rule_


# --------------------------------------------------------------------------- #
# proposed -> shadow
# --------------------------------------------------------------------------- #


def test_a_freshly_induced_rule_goes_to_shadow_and_not_to_active() -> None:
    """No rule automates on the strength of one example."""
    moved = advance(rule(), batch=1, cfg=CFG)
    assert moved.state is RuleState.SHADOW
    assert moved.transitions[-1].reason.startswith("induced from")


def test_a_proposed_rule_cannot_reach_active_in_one_step_however_good_its_record() -> None:
    loaded = judged(rule(), correct=50, wrong=0)
    assert advance(loaded, batch=1, cfg=CFG).state is RuleState.SHADOW


# --------------------------------------------------------------------------- #
# shadow -> active
# --------------------------------------------------------------------------- #


def test_promotion_needs_both_the_confirmations_and_the_precision() -> None:
    minimum = CFG.promotion_min_confirmations
    enough = judged(rule(RuleState.SHADOW), correct=minimum, wrong=0)
    assert advance(enough, batch=3, cfg=CFG).state is RuleState.ACTIVE

    too_few = judged(rule(RuleState.SHADOW), correct=minimum - 1, wrong=0)
    assert advance(too_few, batch=3, cfg=CFG).state is RuleState.SHADOW


def test_a_rule_below_the_precision_bar_stays_in_shadow_however_many_confirmations() -> None:
    """Volume is not evidence. 20 right and 5 wrong is 80%, under the 90% bar."""
    busy = judged(rule(RuleState.SHADOW), correct=20, wrong=5)
    assert busy.precision < CFG.promotion_min_precision
    assert advance(busy, batch=4, cfg=CFG).state is RuleState.SHADOW


def test_promotion_records_the_numbers_that_justified_it() -> None:
    promoted = advance(judged(rule(RuleState.SHADOW), correct=6, wrong=0), batch=3, cfg=CFG)
    transition = promoted.transitions[-1]
    assert transition.to_state == "active"
    assert "6 confirmations" in transition.reason


# --------------------------------------------------------------------------- #
# -> retired
# --------------------------------------------------------------------------- #


def test_a_rule_retires_itself_once_it_has_been_wrong_enough_times() -> None:
    failing = judged(rule(RuleState.ACTIVE), correct=2, wrong=3)
    assert len(failing.judged) >= CFG.retirement_min_observations
    assert failing.precision < CFG.retirement_precision_floor

    retired = advance(failing, batch=5, cfg=CFG)
    assert retired.state is RuleState.RETIRED
    assert "below the" in retired.transitions[-1].reason


def test_a_bad_run_that_is_too_short_to_judge_does_not_retire_a_rule() -> None:
    """One unlucky batch is not evidence either. The floor needs observations."""
    unlucky = judged(rule(RuleState.ACTIVE), correct=0, wrong=CFG.retirement_min_observations - 1)
    assert advance(unlucky, batch=5, cfg=CFG).state is RuleState.ACTIVE


def test_retirement_is_checked_before_promotion() -> None:
    """A record that qualifies for both goes to retired. A rule doing badly enough to
    retire must not be promoted by the same numbers."""
    conflicted = judged(rule(RuleState.SHADOW), correct=CFG.promotion_min_confirmations, wrong=9)
    assert conflicted.confirmations >= CFG.promotion_min_confirmations
    assert conflicted.precision < CFG.retirement_precision_floor
    assert advance(conflicted, batch=5, cfg=CFG).state is RuleState.RETIRED


def test_a_retired_rule_stays_retired() -> None:
    revived = judged(rule(RuleState.RETIRED), correct=20, wrong=0)
    assert advance(revived, batch=8, cfg=CFG).state is RuleState.RETIRED


# --------------------------------------------------------------------------- #
# The record itself
# --------------------------------------------------------------------------- #


def test_unjudged_predictions_do_not_count_towards_precision() -> None:
    """A shadow prediction nobody has ruled on is not a success. Counting it as one
    is the single easiest way to manufacture a promotion."""
    pending = rule(RuleState.SHADOW).observing(
        Observation(batch=2, case_id="case-02-ord_1", predicted_cause="commission_rate_stale",
                    state_at_prediction="shadow")
    )
    assert pending.support == 1
    assert pending.judged == ()
    assert pending.precision is None
    assert advance(pending, batch=2, cfg=CFG).state is RuleState.SHADOW


def test_only_the_first_verdict_on_a_prediction_counts() -> None:
    once = judged(rule(RuleState.SHADOW), correct=1, wrong=0)
    twice = once.judging("case-01-ord_0", False, "operator_card")
    assert twice.confirmations == 1 and twice.refutations == 0


# --------------------------------------------------------------------------- #
# The card decision path
# --------------------------------------------------------------------------- #


def _loop_fixtures():
    """A store with one shadow rule that has predicted, and an operator log."""
    from pipeline.learn import _apply_card_decisions
    from pipeline.rules.resolutions import ACCEPT, DECLINE, CardDecision, OperatorLog
    from pipeline.rules.store import RuleStore

    predicted = rule(RuleState.ACTIVE)
    for index in range(4):
        predicted = predicted.observing(
            Observation(batch=6, case_id=f"case-06-ord_{index}",
                        predicted_cause=predicted.cause, state_at_prediction="active")
        )
    return _apply_card_decisions, RuleStore(rules=[predicted]), OperatorLog, CardDecision, ACCEPT, DECLINE


def test_accepting_a_card_confirms_every_prediction_behind_it() -> None:
    """One click instead of N. It has to count as N confirmations, not one."""
    apply_cards, store, log_cls, decision_cls, accept, _ = _loop_fixtures()
    log = log_cls(resolutions=(), decisions=(
        decision_cls(batch=6, rule_id="R-01", decision=accept, operator="p@e.in"),
    ))
    apply_cards(store, log, batch=6)
    assert store.get("R-01").confirmations == 4
    assert store.get("R-01").refutations == 0


def test_not_this_time_is_a_negative_observation_and_can_retire_a_rule() -> None:
    """The corrigibility path from the checkpoint: declining a card is not a dismissal,
    it moves the rule's live precision and the lifecycle acts on it.

    The shipped run never exercises this — the operator declined no cards, and the one
    retirement there came from their own later resolutions contradicting an over-general
    note. The path is real code either way, so it is driven here rather than left to get
    its first run in front of somebody.
    """
    apply_cards, store, log_cls, decision_cls, _, decline = _loop_fixtures()
    # A fifth prediction, so the record clears retirement_min_observations.
    store.replace(store.get("R-01").observing(
        Observation(batch=6, case_id="case-06-ord_4", predicted_cause="commission_rate_stale",
                    state_at_prediction="active")
    ))
    log = log_cls(resolutions=(), decisions=(
        decision_cls(batch=6, rule_id="R-01", decision=decline, operator="p@e.in",
                     note="over-matching on the promo rows"),
    ))
    apply_cards(store, log, batch=6)

    declined = store.get("R-01")
    assert declined.refutations == 5 and declined.confirmations == 0
    assert declined.precision == D("0.0000")
    assert advance(declined, batch=6, cfg=CFG).state is RuleState.RETIRED


def test_reviewing_a_card_individually_judges_nothing() -> None:
    """'Review individually' is deferral, not a verdict. Counting it either way would
    put an opinion in the record that nobody expressed."""
    from pipeline.rules.resolutions import REVIEW

    apply_cards, store, log_cls, decision_cls, _, _ = _loop_fixtures()
    log = log_cls(resolutions=(), decisions=(
        decision_cls(batch=6, rule_id="R-01", decision=REVIEW, operator="p@e.in"),
    ))
    apply_cards(store, log, batch=6)
    assert store.get("R-01").judged == ()


def test_a_card_decision_only_touches_the_batch_it_was_made_in() -> None:
    apply_cards, store, log_cls, decision_cls, accept, _ = _loop_fixtures()
    log = log_cls(resolutions=(), decisions=(
        decision_cls(batch=7, rule_id="R-01", decision=accept, operator="p@e.in"),
    ))
    apply_cards(store, log, batch=6)
    assert store.get("R-01").judged == ()
