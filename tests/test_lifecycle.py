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

from dataclasses import replace
from decimal import Decimal

import pytest

from pipeline.config import thresholds
from pipeline.rules.lifecycle import advance, lifecycle_config_from
from pipeline.rules.models import Observation, Rule, RuleState

D = Decimal
CFG = lifecycle_config_from(thresholds())


def rule(state: RuleState = RuleState.PROPOSED, **kwargs) -> Rule:
    """A rule that has already cleared the evidence gate, unless a test says otherwise.

    Supplying the demonstrations and the approval by default keeps every test below
    about the transition it is actually testing. The gate itself is tested directly,
    on rules that deliberately lack one or the other, under "the evidence gate".
    """
    defaults: dict = dict(
        rule_id="R-01", cause="commission_rate_stale", resolution_class="internal_fix",
        plain_words="Myntra bills 27.2% against a 25% master rate.", state=state,
        demonstration_ids=tuple(
            f"res_{index:04d}" for index in range(CFG.min_support_demonstrations)
        ),
        approved=True,
        approved_by="priya.n@demostore.in",
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


def test_a_supported_and_approved_rule_goes_to_shadow_and_not_to_active() -> None:
    """No rule automates on the strength of one example."""
    moved = advance(rule(), batch=1, cfg=CFG)
    assert moved.state is RuleState.SHADOW
    assert "operator demonstrations" in moved.transitions[-1].reason


def test_a_proposed_rule_cannot_reach_active_in_one_step_however_good_its_record() -> None:
    loaded = judged(rule(), correct=50, wrong=0)
    assert advance(loaded, batch=1, cfg=CFG).state is RuleState.SHADOW


# --------------------------------------------------------------------------- #
# The evidence gate: proposed -> shadow requires demonstrations *and* a human
# --------------------------------------------------------------------------- #


def test_a_rule_with_one_demonstration_can_never_reach_shadow() -> None:
    """The acceptance check, asserted directly.

    One demonstration is the case the rule was induced from. A rule built on it
    backtests perfectly on that row by construction, so no backtest number and no
    amount of approval may buy a way past this.
    """
    anecdote = rule(demonstration_ids=("res_0001",), approved=True)
    assert advance(anecdote, batch=1, cfg=CFG).state is RuleState.PROPOSED


def test_no_record_however_good_lifts_a_rule_over_the_support_gate() -> None:
    """Fifty correct predictions do not substitute for a second human demonstration."""
    anecdote = judged(rule(demonstration_ids=("res_0001",)), correct=50, wrong=0)
    assert advance(anecdote, batch=4, cfg=CFG).state is RuleState.PROPOSED


def test_support_counts_demonstrations_and_not_rows() -> None:
    """Eighty rows cleared by one sentence is one piece of evidence, not eighty."""
    many_rows = judged(rule(demonstration_ids=("res_0001",)), correct=80, wrong=0)
    assert many_rows.support == 80
    assert many_rows.demonstration_support == 1
    assert advance(many_rows, batch=4, cfg=CFG).state is RuleState.PROPOSED


def test_the_same_resolution_cannot_be_counted_twice() -> None:
    """Otherwise one sentence walks a rule through a gate that asks for several."""
    once = rule(demonstration_ids=()).demonstrated_by("res_0001")
    assert once.demonstrated_by("res_0001").demonstration_support == 1


def test_an_unapproved_rule_waits_however_well_supported_it_is() -> None:
    """Nobody looked at it, so it does not start watching. Silence is not consent."""
    unapproved = rule(approved=False)
    assert unapproved.demonstration_support >= CFG.min_support_demonstrations
    assert advance(unapproved, batch=2, cfg=CFG).state is RuleState.PROPOSED


def test_approval_is_recorded_against_the_rule_and_does_not_move_it() -> None:
    """Approval says 'worth watching'. The thresholds decide when watching starts."""
    approved = rule(approved=False).approving("priya.n@demostore.in", batch=2, note="yes")
    assert approved.approved and approved.approved_by == "priya.n@demostore.in"
    assert approved.state is RuleState.PROPOSED
    assert "approved the candidate card" in approved.transitions[-1].reason


def test_both_conditions_together_are_what_open_the_gate() -> None:
    assert advance(rule(), batch=2, cfg=CFG).state is RuleState.SHADOW


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
# active -> shadow: demotion on human override
# --------------------------------------------------------------------------- #


def overridden(rule_: Rule, times: int) -> Rule:
    """``times`` human corrections of this rule while it was acting."""
    for index in range(times):
        case_id = f"case-09-ord_over{index}"
        rule_ = rule_.observing(
            Observation(batch=9, case_id=case_id, predicted_cause=rule_.cause,
                        state_at_prediction=RuleState.ACTIVE.value)
        ).judging(case_id, False, "human_resolution")
    return rule_


def test_an_active_rule_at_the_override_count_is_demoted_to_shadow() -> None:
    """The acceptance check. Fast human disagreement, not slow statistical decay."""
    corrected = overridden(rule(RuleState.ACTIVE), CFG.max_overrides_before_demotion)
    moved = advance(corrected, batch=9, cfg=CFG)
    assert moved.state is RuleState.SHADOW
    assert "human overrides while active" in moved.transitions[-1].reason


def test_one_override_short_of_the_count_keeps_acting() -> None:
    nearly = overridden(rule(RuleState.ACTIVE), CFG.max_overrides_before_demotion - 1)
    assert advance(nearly, batch=9, cfg=CFG).state is RuleState.ACTIVE


def test_demotion_needs_no_redeploy_only_a_different_number() -> None:
    """The acceptance check says "without a redeploy". The threshold is config, and
    the same rule object demotes or does not purely on what the config says."""
    corrected = overridden(rule(RuleState.ACTIVE), 2)
    strict = replace(CFG, max_overrides_before_demotion=2)
    lenient = replace(CFG, max_overrides_before_demotion=5)
    assert advance(corrected, batch=9, cfg=strict).state is RuleState.SHADOW
    assert advance(corrected, batch=9, cfg=lenient).state is RuleState.ACTIVE


def test_being_wrong_in_shadow_is_not_an_override() -> None:
    """Shadow is where a rule is allowed to be wrong. Charging those to the demotion
    tally would demote a rule for mistakes made before it could do anything."""
    in_shadow = judged(rule(RuleState.SHADOW), correct=0, wrong=4)
    assert in_shadow.overrides == 0
    assert in_shadow.overrides_since_demotion == 0


def test_the_overrides_that_caused_a_demotion_are_not_charged_twice() -> None:
    """Otherwise a demoted rule falls straight back out of active on re-promotion."""
    corrected = overridden(rule(RuleState.ACTIVE), CFG.max_overrides_before_demotion)
    demoted = advance(corrected, batch=9, cfg=CFG)
    assert demoted.overrides_since_demotion == 0

    back = replace(demoted, state=RuleState.ACTIVE)
    assert advance(back, batch=10, cfg=CFG).state is RuleState.ACTIVE


def test_a_demoted_rule_can_earn_its_way_back_through_the_ordinary_gate() -> None:
    """Demotion is recoverable. That is the difference from retirement."""
    corrected = overridden(rule(RuleState.ACTIVE), CFG.max_overrides_before_demotion)
    demoted = advance(corrected, batch=9, cfg=CFG)
    assert demoted.state is RuleState.SHADOW

    redeemed = judged(demoted, correct=40, wrong=0)
    assert advance(redeemed, batch=12, cfg=CFG).state is RuleState.ACTIVE


def test_retirement_beats_demotion_when_a_rule_qualifies_for_both() -> None:
    """Terminal decay outranks a recoverable correction. Most severe wins."""
    both = overridden(judged(rule(RuleState.ACTIVE), correct=0, wrong=5), 2)
    assert advance(both, batch=9, cfg=CFG).state is RuleState.RETIRED


def test_demotion_and_retirement_are_both_available_and_are_different() -> None:
    """Both transitions exist; they are not two names for one mechanism."""
    demoted = advance(overridden(rule(RuleState.ACTIVE), 2), batch=9, cfg=CFG)
    retired = advance(judged(rule(RuleState.ACTIVE), correct=1, wrong=4), batch=9, cfg=CFG)
    assert demoted.state is RuleState.SHADOW
    assert retired.state is RuleState.RETIRED


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
    """One unlucky batch is not evidence enough to *retire*. The floor needs observations.

    It is enough to demote, and that is the two signals doing different jobs: the rule
    stops acting immediately because humans keep correcting it, but it is not written
    off on a sample too small to mean anything. It goes back to shadow, where it keeps
    predicting and can earn its way out.
    """
    unlucky = judged(rule(RuleState.ACTIVE), correct=0, wrong=CFG.retirement_min_observations - 1)
    moved = advance(unlucky, batch=5, cfg=CFG)
    assert moved.state is not RuleState.RETIRED
    assert moved.state is RuleState.SHADOW


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
