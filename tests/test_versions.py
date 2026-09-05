"""Every artifact that acts carries a version, and every automated action names it.

An id alone cannot answer "what did this rule say when it closed that row?" once the
rule has been edited since. That question is the whole of I3, and a provenance record
that only carries ``R-14`` cannot answer it.
"""

from __future__ import annotations

from decimal import Decimal

from pipeline.metrics.pins import Pin, stale
from pipeline.metrics.registry import METRICS, MetricParams, REGISTRY, get
from pipeline.rules.models import Rule, RuleState

D = Decimal


def rule(**kwargs) -> Rule:
    defaults: dict = dict(
        rule_id="R-01", cause="commission_rate_stale", resolution_class="internal_fix",
        plain_words="Myntra bills a higher slab than the master rate says.",
        variance_band_pct=(D("5.0"), D("12.0")),
    )
    return Rule(**{**defaults, **kwargs})


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def test_a_rule_starts_at_version_one() -> None:
    assert rule().version == 1


def test_narrowing_a_rule_bumps_its_version() -> None:
    """It changes what the rule does, so a decision taken under the old band must not
    read as though it were taken under this one."""
    narrowed = rule().narrowed((D("7.0"), D("9.0")), batch=4, note="over-matching")
    assert narrowed.version == 2


def test_a_state_move_does_not_bump_the_version() -> None:
    """Promotion is something that happened to the rule, not a change to the rule."""
    moved = rule().moving_to(RuleState.SHADOW, batch=2, reason="approved")
    assert moved.version == 1


def test_an_observation_does_not_bump_the_version() -> None:
    from pipeline.rules.models import Observation

    observed = rule().observing(
        Observation(batch=2, case_id="case-02-x", predicted_cause="commission_rate_stale",
                    state_at_prediction="shadow")
    )
    assert observed.version == 1


def test_the_version_survives_a_round_trip_through_the_store(tmp_path) -> None:
    from pipeline.rules.store import RuleStore, load, save

    edited = rule().narrowed((D("7.0"), D("9.0")), batch=4, note="tightened")
    path = tmp_path / "rules.json"
    save(RuleStore(rules=[edited]), path)
    assert load(path).get("R-01").version == edited.version == 2


def test_a_rules_json_payload_names_its_version() -> None:
    assert rule().to_json()["version"] == 1


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def test_every_auto_resolution_names_the_rule_version_that_fired(scored) -> None:
    """The acceptance check for I3: the action record carries the id *and* the version."""
    seen = 0
    for batch in scored.run.batches:
        for decision in batch.auto_resolved:
            provenance = decision.provenance
            assert provenance.rule_id is not None
            assert provenance.rule_version is not None, provenance.case_id
            assert provenance.rule_version >= 1
            seen += 1
    assert seen > 0, "no auto-resolutions in the run to check"


def test_the_version_in_a_provenance_record_is_the_rules_own(scored) -> None:
    for batch in scored.run.batches:
        for decision in batch.auto_resolved:
            rule_id = decision.provenance.rule_id
            assert rule_id is not None
            assert decision.provenance.rule_version == scored.run.store.get(rule_id).version


def test_a_case_no_rule_matched_names_no_version(scored) -> None:
    """Absent, not defaulted to 1. Nothing acted, so nothing has a version to report."""
    for batch in scored.run.batches:
        for decision in batch.decisions:
            if decision.provenance.rule_id is None:
                assert decision.provenance.rule_version is None


# --------------------------------------------------------------------------- #
# Metrics and pins
# --------------------------------------------------------------------------- #


def test_every_registered_metric_carries_a_version() -> None:
    for metric in METRICS:
        assert metric.version >= 1


def test_a_computed_result_names_the_definition_that_produced_it(scored) -> None:
    from pipeline.metrics.registry import compute

    result = compute("net_revenue_by_channel", scored.reporting.corpus, MetricParams())
    assert result.version == get("net_revenue_by_channel").version
    assert result.to_json()["version"] == result.version


def test_the_catalogue_publishes_the_version() -> None:
    from pipeline.metrics.registry import catalogue

    for entry in catalogue():
        assert entry["version"] == REGISTRY[entry["metric_id"]].version


def test_a_pin_records_the_definition_it_was_pinned_under() -> None:
    from pipeline.metrics.pins import load

    for pin in load():
        assert pin.metric_version >= 1


def test_a_pin_on_a_moved_definition_is_reported_rather_than_upgraded() -> None:
    """Keeping the name and quietly serving a different number under it is the
    failure. Say the definition moved and let a human look."""
    pinned = Pin(
        pin_id="pin-test", name="Net revenue", metric_id="net_revenue_by_channel",
        params=MetricParams(), pinned_by="priya.n@demostore.in", pinned_at="2025-01-19",
        source_question="how much did we get paid?", metric_version=0,
    )
    flagged = stale([pinned])
    assert flagged == [(pinned, get("net_revenue_by_channel").version)]


def test_the_shipped_pins_are_all_current() -> None:
    from pipeline.metrics.pins import load

    assert stale(load()) == []
