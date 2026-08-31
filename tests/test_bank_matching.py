"""N:1 bank matching and the bounded residual search.

Hand-built groups, because the point of these tests is to pin the behaviour at the
edges the generated corpus does not reach: an unexplainable shortfall, a group too
big to search exhaustively, a zero-value payout that is never wired.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from pipeline.matcher.bank import find_excess_rows, group_by_utr, reconcile_bank, reconcile_group
from pipeline.matcher.normalise import normalise_all
from pipeline.matcher.settings import MatchConfig
from pipeline.models import BankRow, Channel, SettlementRow, TransactionType

D = Decimal

CFG = MatchConfig(
    rounding_tolerance_inr=D("1.00"), date_window_days=21,
    fee_variance_tolerance_pct=D("0.5"), subset_max_size=3, subset_max_candidates=60,
)


def payment(entity_id: str, net: Decimal, utr: str = "AXISN1") -> SettlementRow:
    return SettlementRow(
        entity_id=entity_id, type=TransactionType.PAYMENT, channel=Channel.AMAZON,
        order_id=f"ord_{entity_id}", amount=net, fee=D("0.00"), tax=D("0.00"),
        tcs=D("0.00"), tds=D("0.00"), debit=D("0.00"), credit=net,
        settlement_id="AMZ-STL-1", settlement_utr=utr,
        created_at=date(2025, 1, 1), settled_at=date(2025, 1, 8),
    )


def credit(amount: Decimal, utr: str = "AXISN1") -> BankRow:
    return BankRow(utr=utr, amount=amount, created_at=date(2025, 1, 8))


def rows(*nets: str) -> list:
    return normalise_all([payment(f"st_{i:06d}", D(net)) for i, net in enumerate(nets, 1)])


def test_a_group_that_sums_to_its_credit_ties_out() -> None:
    finding = reconcile_group("AXISN1", rows("100.00", "250.00", "50.00"), credit(D("400.00")), CFG)
    assert finding.ties_out
    assert finding.residual_row_ids == []
    assert finding.shortfall == D("0.00")


def test_paise_drift_across_a_group_stays_inside_the_rounding_tolerance() -> None:
    finding = reconcile_group("AXISN1", rows("100.00", "250.00", "50.00"), credit(D("399.40")), CFG)
    assert finding.ties_out, "60 paise across three rows is not a reconciliation exception"


def test_the_residual_search_names_the_row_the_credit_does_not_cover() -> None:
    """The duplicate-row case: the report claims one row more than the bank funded."""
    finding = reconcile_group(
        "AXISN1", rows("100.00", "250.00", "50.00"), credit(D("350.00")), CFG
    )
    assert not finding.ties_out
    assert finding.shortfall == D("50.00")
    assert finding.residual_row_ids == ["st_000003"]
    assert set(finding.explained_row_ids) == {"st_000001", "st_000002"}


def test_indistinguishable_rows_break_towards_the_later_emission() -> None:
    """First write wins: the second identical row is the re-emission, not the sale."""
    finding = reconcile_group(
        "AXISN1", rows("250.00", "100.00", "250.00"), credit(D("350.00")), CFG
    )
    assert finding.residual_row_ids == ["st_000003"]


def test_an_unexplainable_shortfall_is_reported_with_its_candidates() -> None:
    """No invented explanation. Shortfall, full candidate list, and say so."""
    finding = reconcile_group("AXISN1", rows("100.00", "250.00"), credit(D("300.00")), CFG)
    assert not finding.ties_out
    assert finding.shortfall == D("50.00")
    assert finding.residual_row_ids == []
    assert finding.search_exhausted
    assert finding.candidate_row_ids == ["st_000001", "st_000002"]


def test_the_search_is_bounded_by_max_subset_size() -> None:
    """Four rows summing to the shortfall are past the bound and stay unexplained."""
    tight = MatchConfig(D("1.00"), 21, D("0.5"), subset_max_size=3, subset_max_candidates=60)
    group = rows("10.00", "10.00", "10.00", "10.00", "500.00")
    finding = reconcile_group("AXISN1", group, credit(D("500.00")), tight)
    assert finding.shortfall == D("40.00")
    assert finding.search_exhausted


def test_a_zero_value_payout_with_no_credit_is_not_a_finding() -> None:
    """A same-day capture and refund nets to zero and is never wired."""
    group = normalise_all([payment("st_000001", D("100.00"))])
    group += normalise_all([payment("st_000002", D("-100.00"))])
    finding = reconcile_group("AXISN1", group, None, CFG)
    assert finding.ties_out
    assert finding.bank_amount is None


def test_a_non_zero_group_with_no_credit_puts_every_row_in_the_residual() -> None:
    finding = reconcile_group("AXISN1", rows("100.00", "50.00"), None, CFG)
    assert not finding.ties_out
    assert finding.residual_row_ids == ["st_000001", "st_000002"]


def test_a_credit_with_no_settlement_group_comes_back_as_its_own_finding() -> None:
    findings = reconcile_bank(rows("100.00"), [credit(D("100.00")), credit(D("9000.00"), "UNKN1")], CFG)
    orphan = next(f for f in findings if f.utr == "UNKN1")
    assert orphan.candidate_row_ids == []
    assert orphan.bank_amount == D("9000.00")


def test_grouping_preserves_report_order_within_a_payout() -> None:
    group = group_by_utr(rows("3.00", "1.00", "2.00"))
    assert [r.entity_id for r in group["AXISN1"]] == ["st_000001", "st_000002", "st_000003"]


@pytest.mark.parametrize("shortfall", ["0.00", "-5.00"])
def test_the_excess_search_returns_nothing_when_there_is_nothing_to_find(shortfall: str) -> None:
    assert find_excess_rows(rows("100.00"), D(shortfall), CFG) is None
