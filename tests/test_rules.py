"""Rules: what they may contain, how they match, and which one wins.

Rule matching is the half of the learning loop with no model in it. These tests are
about the properties that make that claim worth anything: the predicate is exact, the
precedence is defensible, and a rule can never be a memorised transaction.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline.cases import CaseFeatures
from pipeline.llm.schemas import InducedRule
from pipeline.rules.models import Rule, RuleState, assert_generalisable, contains_identifier, rule_from
from pipeline.rules.predicates import matches, select, specificity
from pipeline.rules.store import RuleStore, _rule_from_json

D = Decimal


def features(**kwargs) -> CaseFeatures:
    defaults = dict(
        channel="myntra", reason="fee_variance_outside_tolerance", bucket="variance",
        transaction_type=None, direction="short", variance_inr=D("32.73"),
        fee_variance_pct=D("8.80"), net_variance_pct=D("-3.74"),
        days_after_settlement=None, days_since_order=None, days_late=None,
    )
    return CaseFeatures(**{**defaults, **kwargs})


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
# No rule may memorise a transaction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "Myntra is billing 27.2% on ord_000019",
        "The duplicate is st_001110",
        "Credit HDFCN25010900011 has no settlement group",
    ],
)
def test_an_identifier_anywhere_in_a_rule_is_rejected(text: str) -> None:
    """A rule that names a row explains that row and generalises to nothing.

    The schema gives the model nowhere to put an id; this catches the free-text
    fields, where one can arrive without anyone noticing.
    """
    assert contains_identifier(text)
    with pytest.raises(ValueError, match="names a transaction"):
        assert_generalisable([("plain_words", text)])


@pytest.mark.parametrize(
    "text",
    [
        "Myntra bills commission at 27.2% while the master rate says 25%.",
        "A payout correct to the paise that landed 14 days past the window.",
        "Deductions of 0.242 against an expected 0.22.",
    ],
)
def test_percentages_and_rates_are_not_mistaken_for_identifiers(text: str) -> None:
    """The check has to let a rule say what it is about."""
    assert contains_identifier(text) is None
    assert_generalisable([("plain_words", text)])


def test_no_rule_in_the_shipped_store_names_a_transaction() -> None:
    """The done condition, asserted over the real learned rule set."""
    import json
    from pipeline.config import REPO_ROOT

    path = REPO_ROOT / "data" / "rules.json"
    if not path.exists():
        pytest.skip("run `make learn` first")
    for payload in json.loads(path.read_text(encoding="utf-8"))["rules"]:
        for field in ("plain_words", "cause", "resolution_class"):
            assert contains_identifier(str(payload[field])) is None, payload["rule_id"]
        for value in payload["conditions"].values():
            assert contains_identifier(str(value)) is None, payload["rule_id"]


def test_the_induced_rule_schema_has_nowhere_to_put_an_id() -> None:
    """Belt and braces: the model is not asked to behave, it is unable to misbehave."""
    fields = set(InducedRule.model_fields)
    assert not {"order_id", "entity_id", "utr", "settlement_id", "row_id"} & fields


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #


def test_a_rule_matches_only_inside_its_band() -> None:
    assert matches(rule(), features(fee_variance_pct=D("8.80")))
    assert matches(rule(), features(fee_variance_pct=D("9.50")))     # inclusive
    assert not matches(rule(), features(fee_variance_pct=D("9.51")))
    assert not matches(rule(), features(fee_variance_pct=D("7.90")))


def test_a_rule_that_names_a_band_misses_a_row_that_has_no_such_number() -> None:
    """Silence is not evidence. A row with no fee percentage has not satisfied a
    condition about fee percentages; it has failed to answer the question."""
    assert not matches(rule(), features(fee_variance_pct=None))


def test_channel_reason_type_and_direction_all_have_to_hold() -> None:
    assert not matches(rule(), features(channel="amazon"))
    assert not matches(rule(), features(reason="net_variance_outside_tolerance"))
    assert not matches(rule(), features(direction="over"))
    typed = rule(transaction_type="refund")
    assert not matches(typed, features(transaction_type="adjustment"))
    assert matches(typed, features(transaction_type="refund"))


def test_a_rule_with_no_channel_matches_every_channel() -> None:
    assert matches(rule(channel=None), features(channel="amazon"))


def test_lag_windows_are_inclusive_bands_on_days() -> None:
    lagged = rule(variance_band_pct=None, lag_window_days=(8, 21))
    assert matches(lagged, features(fee_variance_pct=None, days_after_settlement=14))
    assert not matches(lagged, features(fee_variance_pct=None, days_after_settlement=7))
    assert not matches(lagged, features(fee_variance_pct=None, days_after_settlement=None))


# --------------------------------------------------------------------------- #
# Precedence
# --------------------------------------------------------------------------- #


def test_the_more_specific_rule_wins() -> None:
    """The narrower rule is the one written about this exact phenomenon."""
    broad = rule(rule_id="R-01", channel=None, variance_band_pct=None)
    narrow = rule(rule_id="R-02")
    assert specificity(narrow) > specificity(broad)
    assert select([broad, narrow], features()).winner is narrow


def test_equally_specific_rules_that_disagree_go_to_a_human() -> None:
    """Not a tie to break. A case the system does not understand."""
    one = rule(rule_id="R-01", cause="commission_rate_stale")
    two = rule(rule_id="R-02", cause="commission_slab_change")
    selection = select([one, two], features())
    assert selection.conflict
    assert selection.winner is None
    assert "disagree" in selection.reason


def test_equally_specific_rules_that_agree_are_not_a_conflict() -> None:
    one = rule(rule_id="R-01")
    two = rule(rule_id="R-02")
    selection = select([one, two], features())
    assert not selection.conflict
    assert selection.winner.rule_id == "R-01"          # stable: lowest id


def test_nothing_matching_is_reported_as_nothing_matching() -> None:
    selection = select([rule()], features(channel="offline"))
    assert selection.winner is None and not selection.conflict


# --------------------------------------------------------------------------- #
# Lifecycle gates on firing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("state", [RuleState.PROPOSED, RuleState.SHADOW, RuleState.RETIRED])
def test_only_an_active_rule_fires(state: RuleState) -> None:
    assert not rule(state=state).fires
    assert rule(state=RuleState.ACTIVE).fires


def test_a_disabled_rule_never_fires_whatever_its_state() -> None:
    """The operator's off switch outranks the lifecycle."""
    assert not rule(state=RuleState.ACTIVE, enabled=False).fires


def test_a_store_only_predicts_with_shadow_and_active_rules() -> None:
    store = RuleStore(rules=[
        rule(rule_id="R-01", state=RuleState.PROPOSED),
        rule(rule_id="R-02", state=RuleState.SHADOW),
        rule(rule_id="R-03", state=RuleState.ACTIVE),
        rule(rule_id="R-04", state=RuleState.RETIRED),
    ])
    assert [r.rule_id for r in store.predicting] == ["R-02", "R-03"]
    assert [r.rule_id for r in store.firing] == ["R-03"]


# --------------------------------------------------------------------------- #
# Corrigibility
# --------------------------------------------------------------------------- #


def test_narrowing_a_band_is_allowed_and_widening_it_is_not() -> None:
    """The human stays in charge, and 'in charge' does not include loosening a rule
    into over-matching by calling it a correction."""
    original = rule()
    tightened = original.narrowed((D("8.5"), D("9.0")), batch=6, note="over-matching on promos")
    assert tightened.variance_band_pct == (D("8.5"), D("9.0"))
    assert "narrowed" in tightened.transitions[-1].reason
    with pytest.raises(ValueError, match="must not widen"):
        original.narrowed((D("5.0"), D("20.0")), batch=6, note="nope")


def test_a_rule_survives_a_round_trip_through_the_store() -> None:
    original = rule(transaction_type="refund", lag_window_days=(1, 21),
                    action_value=D("0.272"), action_field="expected_commission_rate")
    assert _rule_from_json(original.to_json()) == original
