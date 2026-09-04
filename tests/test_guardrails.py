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
DEFAULT = CFG.default_ceiling.max_variance_inr


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
    ceiling = DEFAULT
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
    over_ceiling = features(variance_inr=DEFAULT + D("1.00"))
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


# --------------------------------------------------------------------------- #
# Ceilings the business sets: scoped, unambiguous, and never silent
# --------------------------------------------------------------------------- #


def policy(*overrides: dict, default: str = "500.00"):
    """A GuardrailConfig built the way config would build it, through the loader.

    Built from a dict rather than by constructing VarianceCeiling directly, so these
    tests exercise the validation an operator's edit would actually hit.
    """
    return guardrail_config_from({
        "auto_resolution": {
            "max_variance_inr": default,
            "max_variance_overrides": list(overrides),
            "never_auto_resolve_causes": sorted(CFG.never_auto_resolve_causes),
        }
    })


def test_the_shipped_config_sets_no_overrides() -> None:
    """The shipped numbers are the shipped policy. An override committed by accident
    would move every figure in README.md without changing a line of code."""
    assert CFG.overrides == ()
    assert CFG.default_ceiling.is_default


def test_a_cause_scoped_ceiling_governs_only_that_cause() -> None:
    cfg = policy({"cause": "commission_rate_stale", "max_variance_inr": "1500.00"})
    stale, other = rule(), rule(cause="fee_mismatch_other")
    assert cfg.ceiling_for(stale, features()).max_variance_inr == D("1500.00")
    assert cfg.ceiling_for(other, features()).max_variance_inr == D("500.00")
    assert evaluate(stale, features(variance_inr=D("1400.00")), cfg).held is False
    assert evaluate(other, features(variance_inr=D("1400.00")), cfg).held is True


def test_a_channel_scoped_ceiling_governs_only_that_channel() -> None:
    cfg = policy({"channel": "offline", "max_variance_inr": "0.00"})
    offline = features(channel="offline")
    assert evaluate(rule(channel="offline"), offline, cfg).held is True
    assert evaluate(rule(), features(), cfg).held is False


def test_a_zero_ceiling_turns_auto_resolution_off_for_its_scope() -> None:
    """Nothing is 'within' ₹0, including a paise-level rounding difference."""
    cfg = policy({"channel": "offline", "max_variance_inr": "0.00"})
    result = evaluate(rule(channel="offline"), features(channel="offline",
                                                       variance_inr=D("0.01")), cfg)
    assert [c.name for c in result.held_by] == ["max_variance_inr"]


def test_the_most_specific_ceiling_wins_whatever_order_the_file_is_in() -> None:
    rows = [
        {"cause": "commission_rate_stale", "max_variance_inr": "1500.00"},
        {"cause": "commission_rate_stale", "channel": "myntra", "max_variance_inr": "2500.00"},
    ]
    for ordering in (rows, list(reversed(rows))):
        cfg = policy(*ordering)
        myntra = cfg.ceiling_for(rule(), features(channel="myntra"))
        amazon = cfg.ceiling_for(rule(), features(channel="amazon"))
        assert myntra.max_variance_inr == D("2500.00")
        assert amazon.max_variance_inr == D("1500.00")


def test_an_equally_specific_tie_goes_to_the_stricter_ceiling() -> None:
    """A cause-scoped and a channel-scoped ceiling meet on one case. Both are the
    operator's policy and neither is aimed more precisely, so the lower one governs --
    the same direction ``predicates.select`` resolves a rule tie in, for the same
    reason. What must not decide it is which row was appended last."""
    rows = [
        {"cause": "commission_rate_stale", "max_variance_inr": "1500.00"},
        {"channel": "myntra", "max_variance_inr": "900.00"},
    ]
    for ordering in (rows, list(reversed(rows))):
        cfg = policy(*ordering)
        met = cfg.ceiling_for(rule(), features(channel="myntra"))
        assert met.max_variance_inr == D("900.00")
        assert met.channel == "myntra"
        # Away from the tie each ceiling governs its own scope unchanged.
        assert cfg.ceiling_for(rule(), features(channel="amazon")).max_variance_inr == D("1500.00")
        assert cfg.ceiling_for(
            rule(cause="fee_mismatch_other"), features(channel="myntra")
        ).max_variance_inr == D("900.00")


def test_a_more_specific_ceiling_still_wins_even_when_it_is_the_higher_one() -> None:
    """Strictness breaks ties; it does not override aim. An operator who writes a
    cause+channel ceiling has said something about exactly this case."""
    cfg = policy(
        {"channel": "myntra", "max_variance_inr": "100.00"},
        {"cause": "commission_rate_stale", "channel": "myntra", "max_variance_inr": "2500.00"},
    )
    assert cfg.ceiling_for(rule(), features(channel="myntra")).max_variance_inr == D("2500.00")


def test_the_same_scope_set_twice_is_refused() -> None:
    """A tie within one scope has no principled resolution -- it is someone editing a
    ceiling by adding a line instead of changing one."""
    with pytest.raises(ValueError, match="set twice"):
        policy(
            {"channel": "myntra", "max_variance_inr": "900.00"},
            {"channel": "myntra", "max_variance_inr": "100.00"},
        )


@pytest.mark.parametrize("entry,message", [
    ({"cause": "not_a_cause", "max_variance_inr": "1.00"}, "not a cause"),
    ({"channel": "shopify", "max_variance_inr": "1.00"}, "not a channel"),
    ({"cause": "rounding_variance"}, "no max_variance_inr"),
    ({"max_variance_inr": "1.00"}, "neither a cause nor a channel"),
    ({"cause": "rounding_variance", "max_variance_inr": "-1.00"}, "cannot be negative"),
    ({"cause": "rounding_variance", "max_variance_inr": "1.00", "chanel": "myntra"},
     "unknown field"),
])
def test_a_ceiling_that_could_never_fire_is_refused_at_load(entry, message) -> None:
    """A typo'd cause presents as a ceiling that silently never applies, which is the
    worst way for a risk threshold to be wrong -- so it is a load error, not a shrug."""
    with pytest.raises(ValueError, match=message):
        policy(entry)


def test_the_detail_line_names_the_ceiling_and_who_set_it() -> None:
    """A resolution has to be able to say under whose policy it closed."""
    cfg = policy({
        "cause": "commission_rate_stale", "max_variance_inr": "1500.00",
        "set_by": "finance.head@demostore.in",
    })
    detail = evaluate(rule(), features(variance_inr=D("2000.00")), cfg).checks[0].detail
    assert "₹1500.00" in detail
    assert "cause=commission_rate_stale" in detail
    assert "finance.head@demostore.in" in detail

    default_detail = evaluate(rule(), features(), CFG).checks[0].detail
    assert "default ceiling" in default_detail


def test_the_governing_ceiling_travels_with_the_result() -> None:
    cfg = policy({"cause": "commission_rate_stale", "max_variance_inr": "1500.00"})
    assert evaluate(rule(), features(), cfg).ceiling.cause == "commission_rate_stale"
    assert evaluate(rule(), features(), CFG).ceiling.is_default


def test_a_what_if_default_keeps_the_standing_overrides() -> None:
    """--max-variance-inr asks about the cases nobody has written a ceiling for. It is
    not a way to wipe the policy from the command line."""
    cfg = policy({"channel": "offline", "max_variance_inr": "0.00"})
    moved = cfg.with_default_ceiling(D("2000.00"))
    assert moved.default_ceiling.max_variance_inr == D("2000.00")
    assert moved.overrides == cfg.overrides
    assert evaluate(rule(channel="offline"), features(channel="offline",
                                                     variance_inr=D("50.00")), moved).held


def test_a_negative_what_if_ceiling_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        CFG.with_default_ceiling(D("-1.00"))


def test_a_scoped_ceiling_cannot_lift_a_blocked_cause_or_class() -> None:
    """The ceiling is one of three guardrails, not a master switch. Setting a generous
    number for a chargeback does not make chargebacks automatable."""
    cfg = policy({"cause": "chargeback_deduction", "max_variance_inr": "99999.00"})
    result = evaluate(
        rule(cause="chargeback_deduction", resolution_class="counterparty_claim"),
        features(variance_inr=D("1.00")), cfg,
    )
    assert result.held
    assert [c.name for c in result.held_by] == ["never_auto_resolve", "resolution_class"]
