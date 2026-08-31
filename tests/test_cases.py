"""Exception cases: the unit everything downstream agrees on.

The matcher's unit is the row and a human's is not. If this grouping is wrong then
the queue, the rules and the review rate are all counting different things while
appearing to agree.
"""

from __future__ import annotations

from decimal import Decimal
from datetime import date

import pytest

from pipeline.cases import (
    ExceptionCase,
    FindingLog,
    build_cases,
    case_key,
    features_of,
    finding_key,
    is_open,
)
from pipeline.matcher import BatchResult, Bucket, Reason, Verdict

D = Decimal


def verdict(**kwargs) -> Verdict:
    defaults = dict(
        table="settlement_report", row_id="st_1", bucket=Bucket.VARIANCE,
        reason=Reason.FEE_OUTSIDE_TOLERANCE, detail={}, order_id="ord_1",
        channel="myntra", impact_inr=D("0.00"),
    )
    return Verdict(**{**defaults, **kwargs})


def result(verdicts: list[Verdict], batch: int = 1) -> BatchResult:
    return BatchResult(batch=batch, verdicts=verdicts, groups=[], settled_orders={})


# --------------------------------------------------------------------------- #
# Grouping
# --------------------------------------------------------------------------- #


def test_an_orders_settlement_and_ledger_verdicts_are_one_case() -> None:
    """A bookkeeper works a wrong commission rate once, not twice."""
    cases = build_cases(
        result([
            verdict(table="settlement_report", row_id="st_1", order_id="ord_1"),
            verdict(table="internal_ledger", row_id="ord_1", order_id="ord_1",
                    impact_inr=D("32.73")),
        ])
    )
    assert len(cases) == 1
    assert cases[0].kind == "order"
    assert cases[0].impact_inr == D("32.73")
    assert cases[0].settlement_row_ids == ("st_1",)


def test_a_bank_credit_is_its_own_case_even_though_it_has_no_channel() -> None:
    cases = build_cases(
        result([
            verdict(table="bank_statement", row_id="HDFC1", order_id=None, channel=None,
                    reason=Reason.BANK_CREDIT_NO_SETTLEMENT_GROUP, bucket=Bucket.UNMATCHED,
                    impact_inr=D("17625.00")),
        ])
    )
    assert [(c.kind, c.key) for c in cases] == [("bank_credit", "HDFC1")]


def test_a_row_with_no_order_stands_alone() -> None:
    assert case_key(verdict(order_id=None, row_id="st_9")) == ("row", "st_9")


def test_orders_inside_their_settlement_window_never_reach_a_queue() -> None:
    """Carried, not queued. Nobody works a payout that is not due yet."""
    carried = verdict(
        table="internal_ledger", row_id="ord_2", bucket=Bucket.UNMATCHED,
        reason=Reason.AWAITING_SETTLEMENT_IN_WINDOW,
    )
    assert not is_open(carried)
    assert build_cases(result([carried])) == []


def test_a_matched_row_is_not_an_exception() -> None:
    assert not is_open(verdict(bucket=Bucket.MATCHED, reason=Reason.ORDER_MATCHED_CLEAN))


# --------------------------------------------------------------------------- #
# Aging
# --------------------------------------------------------------------------- #


def test_the_same_finding_is_queued_once_and_then_never_again() -> None:
    """An order overdue in batch 5 and never paid is one problem, not six."""
    log = FindingLog()
    overdue = verdict(
        table="internal_ledger", row_id="ord_3", bucket=Bucket.UNMATCHED,
        reason=Reason.SETTLEMENT_OVERDUE, impact_inr=D("2500.00"),
    )
    first = build_cases(result([overdue], batch=5), log)
    again = build_cases(result([overdue], batch=6), log)
    assert len(first) == 1
    assert again == []


def test_the_queue_and_the_harness_agree_about_what_a_finding_is() -> None:
    """One definition, imported by both. A silent divergence here would make the
    review-rate curve a statement about the counting rule."""
    from harness import aging

    v = verdict()
    assert finding_key(v) == (v.table, v.row_id, v.reason.value)
    index = aging.index([result([v], batch=3)])
    assert index.is_new(3, v)
    assert not index.is_new(4, v)


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #


def test_no_feature_a_rule_can_read_is_an_identifier() -> None:
    """The negative constraint the whole learning loop rests on.

    A rule is a predicate over CaseFeatures. If an identifier appeared here, a rule
    could memorise a transaction without any validator noticing, because it would
    never have to write the id down.
    """
    features = features_of([verdict(detail={"fee_delta": "27.74", "expected_fee": "315.25"})])
    for name, value in vars(features).items():
        assert "ord_" not in str(value) and "st_" not in str(value), name
    assert not {"order_id", "entity_id", "utr", "row_id"} & set(vars(features))


def test_direction_reads_the_net_before_the_fee() -> None:
    """A platform that overcharged commission short-paid the seller by that amount."""
    assert features_of([verdict(detail={"net_delta": "-32.73", "fee_delta": "27.74"})]).direction == "short"
    assert features_of([verdict(detail={"fee_delta": "27.74"})]).direction == "short"
    assert features_of([verdict(detail={"net_delta": "19.51"})]).direction == "over"
    assert features_of([verdict(detail={})]).direction == "flat"


def test_the_guardrail_number_measures_the_error_not_the_order() -> None:
    """``max_variance_inr`` must not block on the size of the sale.

    A ₹30 rate variance on a ₹4,000 order is a ₹30 problem. Reading ``expected_net``
    here would refuse to automate it, which is a guardrail about the wrong quantity.
    """
    features = features_of([
        verdict(detail={"net_delta": "-32.73", "fee_delta": "27.74", "expected_net": "3917.32"})
    ])
    assert features.variance_inr == D("32.73")


def test_an_order_that_never_settled_falls_back_to_the_whole_expected_payout() -> None:
    features = features_of([
        verdict(bucket=Bucket.UNMATCHED, reason=Reason.SETTLEMENT_OVERDUE,
                detail={"expected_net": "2500.00"})
    ])
    assert features.variance_inr == D("2500.00")


def test_percentages_survive_as_decimals() -> None:
    features = features_of([verdict(detail={"fee_variance_pct": "8.80", "net_variance_pct": "-3.74"})])
    assert features.fee_variance_pct == D("8.80")
    assert isinstance(features.fee_variance_pct, Decimal)


def test_case_ids_are_deterministic() -> None:
    """Two runs over the same batch must produce the same case ids or nothing that
    references a case -- a resolution, a rule's provenance -- survives a rerun."""
    first = build_cases(result([verdict()]))
    second = build_cases(result([verdict()]))
    assert [c.case_id for c in first] == [c.case_id for c in second] == ["case-01-ord_1"]
