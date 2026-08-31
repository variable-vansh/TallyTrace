"""The matcher, composed.

``reconcile`` is a pure function: data and config in, verdicts out. No file is read,
no clock is consulted, no global is touched. That is what makes the determinism
claim checkable rather than asserted, and it is why the loader that reads CSVs and
the runner that walks the ten batches both live outside this package.

Order of operations matters and is deliberate:

1. **Bank first.** A row the payout never funded is not money the platform paid, so
   it must be pulled out before the order-level sums are taken. Run the other way
   round, a duplicated settlement row flags its own order as a variance as well as
   itself, and one trouble becomes two exceptions.
2. **Orders next**, over the rows that survive.
3. **Assembly last**, applying bucket precedence so every row ends up with exactly
   one verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Mapping

from pipeline.matcher.bank import reconcile_bank
from pipeline.matcher.normalise import NormalisedRow, ZERO, normalise_all
from pipeline.matcher.orders import OpenOrder, OrderFinding, match_orders
from pipeline.matcher.reasons import Bucket, Reason
from pipeline.matcher.settings import MatchConfig
from pipeline.matcher.verdicts import (
    BatchResult,
    GroupFinding,
    Verdict,
    assert_one_bucket_each,
    detail,
)
from pipeline.models import BankRow, BankStatus, SettlementRow
from pipeline.matcher.quarantine import QuarantineRecord


@dataclass(frozen=True)
class ReconInput:
    """One reconciliation run's universe.

    ``open_orders`` is cumulative: every order booked in batches 1..N that has not
    been settled yet, not just the ones booked this week. A settlement row lands in
    the batch its payout fell into and a ledger row in the batch the order was
    booked in, so reconciling batch N against batch N's ledger alone would leave
    most of the report unmatched by construction. ``closed_order_ids`` is the other
    half of that: orders a previous batch already settled, so a deduction arriving
    against one later is reported as a late row rather than an unknown order.
    """

    batch: int
    batch_end: date
    settlements: list[SettlementRow]
    bank: list[BankRow]
    open_orders: list[OpenOrder]
    closed_orders: Mapping[str, date] = field(default_factory=dict)
    quarantined: list[QuarantineRecord] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Bank stage
# --------------------------------------------------------------------------- #


def _unfunded(findings: list[GroupFinding]) -> dict[str, Reason]:
    """Rows a bank credit does not account for, with the reason they do not fit."""
    unfunded: dict[str, Reason] = {}
    for group in findings:
        if group.ties_out:
            continue
        reason = (
            Reason.SETTLEMENT_GROUP_NO_BANK_CREDIT
            if group.bank_amount is None
            else Reason.NOT_FUNDED_BY_BANK_CREDIT
        )
        for row_id in group.residual_row_ids:
            unfunded[row_id] = reason
    return unfunded


def _bank_verdict(group: GroupFinding, credit: BankRow) -> Verdict:
    if credit.status is BankStatus.REVERSED:
        bucket, reason = Bucket.UNMATCHED, Reason.BANK_CREDIT_REVERSED
    elif not group.candidate_row_ids:
        bucket, reason = Bucket.UNMATCHED, Reason.BANK_CREDIT_NO_SETTLEMENT_GROUP
    elif group.ties_out:
        bucket, reason = Bucket.MATCHED, Reason.BANK_GROUP_TIES_OUT
    else:
        bucket, reason = Bucket.VARIANCE, Reason.BANK_GROUP_SUM_MISMATCH

    return Verdict(
        table="bank_statement",
        row_id=group.utr,
        bucket=bucket,
        reason=reason,
        impact_inr=ZERO if bucket is Bucket.MATCHED else abs(group.shortfall),
        detail=detail(
            settlement_sum=group.settlement_sum,
            bank_amount=group.bank_amount,
            shortfall=group.shortfall,
            rows_in_group=len(group.candidate_row_ids),
            residual_rows=",".join(group.residual_row_ids) or None,
            search_exhausted=group.search_exhausted or None,
        ),
    )


# --------------------------------------------------------------------------- #
# Settlement stage
# --------------------------------------------------------------------------- #


def _order_verdict_detail(finding: OrderFinding) -> dict[str, str]:
    value = finding.value
    if value is None:
        return detail(due_by=finding.due_by, expected_net=finding.impact or None)
    return detail(
        expected_fee=value.expected_fee,
        charged_fee=value.charged_fee,
        fee_delta=value.fee_delta,
        expected_net=value.expected_net,
        settled_net=value.settled_net,
        net_delta=value.net_delta,
        fee_variance_pct=value.fee_variance_pct,
        net_variance_pct=value.net_variance_pct,
        tolerance_inr=value.tolerance,
        days_late=finding.days_late or None,
    )


def _settlement_verdict(
    row: NormalisedRow,
    unfunded: dict[str, Reason],
    findings: dict[str, OrderFinding],
    closed: Mapping[str, date],
) -> Verdict:
    """One settlement row's bucket. Bank findings outrank order findings.

    A row the payout never funded is reported on its own terms rather than folded
    into its order's arithmetic, because it is not money the platform paid.
    """
    bucket, reason, impact, extra = _settlement_finding(row, unfunded, findings, closed)
    return Verdict(
        table="settlement_report",
        row_id=row.entity_id,
        order_id=row.order_id,
        channel=row.row.channel.value,
        bucket=bucket,
        reason=reason,
        impact_inr=impact,
        detail=extra,
    )


def _late_row_detail(row: NormalisedRow, settled_on: date | None) -> dict[str, str]:
    """What a deduction arriving after its order closed can say about itself.

    Both figures are temporal on purpose. Without them the only thing separating a
    lagged refund from an RTO reversal in this corpus is the channel each was
    injected on, and a rule induced on that would score perfectly here while having
    learned nothing about either phenomenon.
    """
    return detail(
        row_net=row.net,
        type=row.row.type.value,
        description=row.row.description,
        days_since_order=(row.row.settled_at - row.row.created_at).days,
        days_after_settlement=(
            None if settled_on is None else (row.row.settled_at - settled_on).days
        ),
    )


def _settlement_finding(
    row: NormalisedRow,
    unfunded: dict[str, Reason],
    findings: dict[str, OrderFinding],
    closed: Mapping[str, date],
) -> tuple[Bucket, Reason, Decimal, dict[str, str]]:
    if row.entity_id in unfunded:
        return (
            Bucket.UNMATCHED, unfunded[row.entity_id], abs(row.net),
            detail(row_net=row.net, settlement_utr=row.utr),
        )
    if row.order_id is None:
        return (
            Bucket.UNMATCHED, Reason.ADJUSTMENT_WITHOUT_ORDER, abs(row.net),
            detail(row_net=row.net, type=row.row.type.value),
        )
    finding = findings.get(row.order_id)
    if finding is not None:
        # Impact is carried on the order's ledger verdict so that summing rupees
        # across verdicts does not count one variance once per row.
        return finding.bucket, finding.reason, ZERO, _order_verdict_detail(finding)
    if row.order_id in closed:
        return (
            Bucket.UNMATCHED, Reason.LATE_ROW_FOR_SETTLED_ORDER, abs(row.net),
            _late_row_detail(row, closed[row.order_id]),
        )
    return (
        Bucket.UNMATCHED, Reason.ROW_FOR_UNKNOWN_ORDER, abs(row.net),
        _late_row_detail(row, None),
    )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _quarantine_verdicts(records: list[QuarantineRecord]) -> list[Verdict]:
    return [
        Verdict(
            table=record.table, row_id=record.row_id, bucket=Bucket.QUARANTINED,
            reason=record.reason, detail=detail(message=record.message),
        )
        for record in records
    ]


def _ledger_verdict(finding: OrderFinding) -> Verdict:
    return Verdict(
        table="internal_ledger", row_id=finding.order_id, order_id=finding.order_id,
        channel=finding.channel, bucket=finding.bucket, reason=finding.reason,
        impact_inr=finding.impact, detail=_order_verdict_detail(finding),
    )


def reconcile(inp: ReconInput, cfg: MatchConfig) -> BatchResult:
    """Bucket every row of one batch. Pure: same input, same output, always."""
    rows = normalise_all(inp.settlements)
    groups = reconcile_bank(rows, inp.bank, cfg)
    unfunded = _unfunded(groups)

    rows_by_order: dict[str, list[NormalisedRow]] = {}
    for row in rows:
        if row.order_id is not None and row.entity_id not in unfunded:
            rows_by_order.setdefault(row.order_id, []).append(row)

    findings = match_orders(inp.open_orders, rows_by_order, inp.batch_end, cfg)
    by_order = {finding.order_id: finding for finding in findings}
    credits = {credit.utr: credit for credit in inp.bank}

    verdicts: list[Verdict] = []
    verdicts.extend(_quarantine_verdicts(inp.quarantined))
    verdicts.extend(
        _settlement_verdict(row, unfunded, by_order, inp.closed_orders) for row in rows
    )
    verdicts.extend(
        _bank_verdict(group, credits[group.utr]) for group in groups if group.utr in credits
    )
    verdicts.extend(_ledger_verdict(finding) for finding in findings)
    assert_one_bucket_each(verdicts)

    return BatchResult(
        batch=inp.batch,
        verdicts=verdicts,
        groups=groups,
        settled_orders={
            f.order_id: f.settled_on for f in findings if f.settled and f.settled_on
        },
    )


def total_impact(result: BatchResult, bucket: Bucket | None = None) -> Decimal:
    """Rupees the run put in question, optionally for one bucket."""
    return sum(
        (v.impact_inr for v in result.verdicts if bucket is None or v.bucket is bucket), ZERO
    )
