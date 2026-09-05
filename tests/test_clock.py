"""The daily clock, the buckets, and the idempotency the whole thing rests on.

A claims queue is fed by a scheduled job, and the normal condition of a scheduled job
is being retried. These tests are mostly about what must *not* happen on the second
run.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from pipeline.cases import CaseFeatures, ExceptionCase
from pipeline.claims.clock import (
    AMBER,
    EXPIRED,
    GREEN,
    RED,
    UNCLOCKED,
    BucketConfig,
    bucket_config_from,
    bucket_for,
    is_escalated,
)
from pipeline.claims.deadlines import (
    DAY_OF_NEXT_MONTH,
    DAYS_FROM_EVENT,
    DeadlinePolicyError,
    deadline_config_from,
    deadline_for,
)
from pipeline.config import resolution_class_by_cause, thresholds
from pipeline.learn import new_register

D = Decimal
BUCKETS = bucket_config_from(thresholds())
ROUTES = resolution_class_by_cause()


def _case(case_id: str = "case-01-x", kind: str = "order", key: str = "ord_000001"):
    features = CaseFeatures(
        channel="amazon", reason="settlement_overdue_beyond_window", bucket="unmatched",
        transaction_type=None, direction="short", variance_inr=D("100.00"),
        fee_variance_pct=None, net_variance_pct=None, days_after_settlement=None,
        days_since_order=None, days_late=3,
    )
    return ExceptionCase(case_id=case_id, batch=1, kind=kind, key=key, verdicts=(),
                         features=features, impact_inr=D("100.00"))


def _routed(case_id="case-01-x", kind="order", key="ord_000001"):
    return [(_case(case_id, kind, key), "missing_settlement_row", "hypothesis")]


# --------------------------------------------------------------------------- #
# Buckets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "days,expected",
    [(None, UNCLOCKED), (-1, EXPIRED), (0, RED), (7, RED), (8, AMBER), (14, AMBER), (15, GREEN)],
)
def test_a_claim_lands_in_the_bucket_its_remaining_days_put_it_in(days, expected) -> None:
    assert bucket_for(days, BUCKETS) == expected


def test_the_bucket_boundaries_come_from_config_and_not_from_this_file() -> None:
    assert bucket_for(BUCKETS.red_within_days, BUCKETS) == RED
    assert bucket_for(BUCKETS.red_within_days + 1, BUCKETS) == AMBER
    assert bucket_for(BUCKETS.amber_within_days, BUCKETS) == AMBER
    assert bucket_for(BUCKETS.amber_within_days + 1, BUCKETS) == GREEN


def test_buckets_are_sized_to_the_batch_cadence() -> None:
    """Batches are weekly. A red bucket shorter than one batch would never be seen."""
    assert BUCKETS.red_within_days >= 7


def test_an_amber_window_inside_the_red_one_is_refused_at_load() -> None:
    with pytest.raises(ValueError, match="amber is the wider window"):
        bucket_config_from({"claims": {"buckets": {"red_within_days": 14,
                                                   "amber_within_days": 7}}})


def test_escalation_is_amber_and_red_and_nothing_else() -> None:
    assert is_escalated(RED) and is_escalated(AMBER)
    assert not is_escalated(GREEN)
    assert not is_escalated(UNCLOCKED)


def test_an_unclocked_claim_is_not_urgent_and_is_not_comfortable_either() -> None:
    """It has no window at all, which is a different fact from having plenty of time."""
    assert bucket_for(None, BUCKETS) == UNCLOCKED
    assert not is_escalated(UNCLOCKED)


# --------------------------------------------------------------------------- #
# The clock job is idempotent
# --------------------------------------------------------------------------- #


def test_running_the_clock_twice_in_one_day_is_a_no_op_the_second_time() -> None:
    """The acceptance check. A retry must not expire a claim twice."""
    register = new_register()
    register.advance(1, date(2025, 1, 7), [], _routed(), ROUTES)

    first = register.tick(date(2025, 1, 8), 1)
    second = register.tick(date(2025, 1, 8), 1)

    assert first.ran is True
    assert second.ran is False
    assert second.expired == ()


def test_the_clock_refuses_to_run_backwards() -> None:
    register = new_register()
    register.advance(1, date(2025, 1, 7), [], _routed(), ROUTES)
    register.tick(date(2025, 1, 20), 1)
    assert register.tick(date(2025, 1, 9), 1).ran is False


def test_a_second_tick_does_not_expire_a_claim_a_second_time() -> None:
    """A double expiry would write a second terminal transition onto a closed claim
    and count its rupees into the write-off total twice."""
    register = new_register()
    register.advance(1, date(2025, 1, 7), [], _routed(), ROUTES)
    claim = register.claims[0]
    assert claim.deadline.on is not None
    lapsed = claim.deadline.on + timedelta(days=1)

    first = register.tick(lapsed, 1)
    assert first.expired == (claim.claim_id,)
    after_first = register.get(claim.claim_id)

    again = register.tick(lapsed, 1)
    assert again.expired == ()
    assert register.get(claim.claim_id).transitions == after_first.transitions


# --------------------------------------------------------------------------- #
# The register is idempotent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kind,key", [("order", "ord_000001"), ("bank_credit", "utr-1")])
def test_re_running_a_batch_opens_nothing_new(kind: str, key: str) -> None:
    """Whatever the case shape. The order-keyed path used to be protected by a dedupe
    that non-order cases did not go through, which is not the same as being idempotent."""
    register = new_register()
    routed = _routed(kind=kind, key=key)
    first = register.advance(1, date(2025, 1, 7), [], routed, ROUTES)
    second = register.advance(1, date(2025, 1, 7), [], routed, ROUTES)

    assert first.opened == second.opened
    assert len(register.claims) == 1
    assert len(register.batches) == 1


def test_a_re_run_batch_returns_the_record_it_already_wrote() -> None:
    register = new_register()
    first = register.advance(1, date(2025, 1, 7), [], _routed(), ROUTES)
    assert register.advance(1, date(2025, 1, 7), [], _routed(), ROUTES) is first


# --------------------------------------------------------------------------- #
# The deadline policy is a table
# --------------------------------------------------------------------------- #


def test_a_channel_window_is_a_duration_from_the_day_it_opened() -> None:
    cfg = deadline_config_from(thresholds())
    clock = deadline_for("missing_settlement_row", "amazon", date(2025, 1, 19), cfg)
    assert clock.on == date(2025, 2, 18)


def test_a_claim_type_row_beats_a_channel_row_on_a_tie() -> None:
    """A TCS discrepancy on Flipkart is on the GSTR-8 calendar, not Flipkart's window.
    Taking the channel row would quietly hand it twenty days it does not have."""
    cfg = deadline_config_from(thresholds())
    governing = cfg.governing("tcs_timing_mismatch", "flipkart")
    assert governing is not None
    assert governing.rule == DAY_OF_NEXT_MONTH
    assert deadline_for("tcs_timing_mismatch", "flipkart", date(2025, 1, 19), cfg).on == (
        date(2025, 2, 10)
    )


def test_adding_a_channel_is_adding_a_row() -> None:
    """The point of the table. No code names a channel."""
    cfg = deadline_config_from({
        "claims": {"deadline_policy": [
            {"channel": "shopify", "rule": DAYS_FROM_EVENT, "value": 45},
        ]}
    })
    assert deadline_for("missing_settlement_row", "shopify", date(2025, 1, 1), cfg).on == (
        date(2025, 2, 15)
    )


def test_a_scope_with_no_row_gets_no_clock_rather_than_a_default() -> None:
    cfg = deadline_config_from(thresholds())
    assert deadline_for("chargeback_deduction", "website", date(2025, 1, 19), cfg).on is None


def test_an_unknown_rule_type_is_refused_at_load() -> None:
    with pytest.raises(DeadlinePolicyError, match="unknown deadline rule"):
        deadline_config_from({"claims": {"deadline_policy": [
            {"channel": "amazon", "rule": "whenever_we_get_round_to_it", "value": 30},
        ]}})


def test_a_row_that_scopes_nothing_is_refused_at_load() -> None:
    with pytest.raises(DeadlinePolicyError, match="must name a channel"):
        deadline_config_from({"claims": {"deadline_policy": [
            {"rule": DAYS_FROM_EVENT, "value": 30},
        ]}})


def test_the_same_scope_twice_is_refused_at_load() -> None:
    with pytest.raises(DeadlinePolicyError, match="same scope"):
        deadline_config_from({"claims": {"deadline_policy": [
            {"channel": "amazon", "rule": DAYS_FROM_EVENT, "value": 30},
            {"channel": "amazon", "rule": DAYS_FROM_EVENT, "value": 60},
        ]}})


def test_the_clocked_causes_come_from_the_table_and_not_from_a_constant() -> None:
    cfg = deadline_config_from(thresholds())
    assert "tcs_timing_mismatch" in cfg.clocked_claim_types
    assert "missing_settlement_row" not in cfg.clocked_claim_types
