"""The specificity ladder: three readings of one note, and only ever wider.

The ladder is the one place in the induction path where code proposes something the
model did not say, so what it is *not* allowed to do matters more than what it does.
It may drop a constraint. It may not add one, tighten one, or change what the rule is
about.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from pipeline.llm.schemas import InducedRule
from pipeline.rules.candidates import (
    GENERAL,
    LEVELS,
    MEDIUM,
    NARROW,
    ladder,
)

D = Decimal


def induced(**kwargs) -> InducedRule:
    defaults: dict = dict(
        channel="myntra",
        cause="commission_rate_stale",
        reason_code="fee_variance_outside_tolerance",
        transaction_type="payment",
        variance_band_pct=(D("7.0"), D("9.0")),
        net_variance_band_pct=(D("-4.0"), D("-3.0")),
        direction="short",
        lag_window_days=(1, 30),
        resolution_class="internal_fix",
        action={"type": "update_ledger_rate", "field": "expected_commission_rate",
                "value": "0.272"},
        plain_words="Myntra bills a higher slab than the master rate sheet says.",
    )
    return InducedRule(**{**defaults, **kwargs})


def test_a_fully_constrained_note_produces_three_rungs() -> None:
    rungs = ladder(induced())
    assert [rung.level for rung in rungs] == [NARROW, MEDIUM, GENERAL]


def test_the_narrow_rung_is_the_models_reading_untouched() -> None:
    """Whatever else the ladder does, it does not edit what the model said."""
    original = induced()
    assert ladder(original)[0].rule == original


def test_each_rung_constrains_no_more_than_the_one_below_it() -> None:
    """The ladder only ever widens. A rung that added a constraint would be a rule
    nobody wrote, firing on rows nobody demonstrated."""
    rungs = ladder(induced())
    for tighter, looser in zip(rungs, rungs[1:]):
        for name in ("variance_band_pct", "net_variance_band_pct",
                     "transaction_type", "lag_window_days"):
            if getattr(tighter, "rule").__getattribute__(name) is None:
                assert getattr(looser.rule, name) is None, name
        if tighter.rule.direction == "any":
            assert looser.rule.direction == "any"


def test_the_general_rung_keeps_the_categorical_shape_and_drops_every_number() -> None:
    general = ladder(induced())[-1]
    assert general.level == GENERAL
    assert general.rule.variance_band_pct is None
    assert general.rule.net_variance_band_pct is None
    assert general.rule.lag_window_days is None
    assert general.rule.direction == "any"
    # What a rule is *about* is never relaxed away.
    assert general.rule.cause == induced().cause
    assert general.rule.channel == induced().channel
    assert general.rule.reason_code == induced().reason_code


def test_the_cause_and_the_action_survive_every_rung() -> None:
    for rung in ladder(induced()):
        assert rung.rule.cause == induced().cause
        assert rung.rule.action == induced().action
        assert rung.rule.resolution_class == induced().resolution_class


def test_an_already_general_note_collapses_rather_than_padding_to_three() -> None:
    """Padding back out to three would mean inventing a constraint to relax."""
    plain = induced(
        transaction_type=None, variance_band_pct=None, net_variance_band_pct=None,
        lag_window_days=None, direction="any",
    )
    rungs = ladder(plain)
    assert len(rungs) == 1
    assert rungs[0].level == NARROW


def test_rungs_are_distinct() -> None:
    rungs = ladder(induced())
    assert len({rung.signature for rung in rungs}) == len(rungs)


@pytest.mark.parametrize("level", LEVELS)
def test_every_level_is_one_of_the_declared_three(level: str) -> None:
    assert level in (NARROW, MEDIUM, GENERAL)
