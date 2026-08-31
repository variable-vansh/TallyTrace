"""Per-batch throughput and accuracy.

Rates are quoted as a percentage of the **settlement report**, because that is the
table whose rows the matcher buckets and the one whose size grows 59 -> 181 across
the corpus. That growth is the point: a review rate quoted as a count would fall
simply because a later batch is bigger, and the curve would be an artifact of the
denominator rather than a claim about the system.

Orders still inside their settlement window are excluded from the review queue. They
are carried, not queued -- nobody works an exception for a payout that is not due
yet -- and counting them would put 118 non-problems in batch 1's queue.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from harness.aging import Aging
from harness.cost import Pricing, cost_inr, per_transaction_inr
from pipeline.llm.usage import LlmUsage
from pipeline.matcher import BatchResult, Bucket, Reason

ZERO = Decimal("0.00")
HUNDRED = Decimal("100")
CARRIED = Reason.AWAITING_SETTLEMENT_IN_WINDOW


def pct(part: int, whole: int) -> Decimal:
    """A percentage to two places. Zero over zero is zero, not an exception."""
    if whole <= 0:
        return ZERO
    return (Decimal(part) * HUNDRED / Decimal(whole)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class BatchMetrics:
    """One batch's numbers. ``seconds`` is the only non-reproducible field here."""

    batch: int
    records_processed: int
    settlement_rows: int
    matched: int
    variance: int
    unmatched: int
    quarantined: int
    review_queue: int             # findings new this batch, all three tables
    aged_findings: int            # the same problem, still open from an earlier batch
    auto_resolved: int            # settlement rows a learned rule closed without a human
    carried_forward: int          # orders inside their window; not exceptions
    exception_impact_inr: Decimal
    usage: LlmUsage
    cost_inr: Decimal
    cost_per_transaction_inr: Decimal
    seconds: float

    @property
    def auto_match_rate(self) -> Decimal:
        return pct(self.matched, self.settlement_rows)

    @property
    def flagged(self) -> int:
        return self.variance + self.unmatched + self.quarantined

    @property
    def review_rate(self) -> Decimal:
        """What the matcher alone leaves for a human, as a percentage of batch total.

        Deliberately unaffected by automation. This is the matcher's own measurement
        and it should stay comparable across every later checkpoint -- if the number
        below moves, this one says whether the matcher or the learning loop moved it.
        """
        return pct(self.flagged, self.settlement_rows)

    @property
    def net_review_rate(self) -> Decimal:
        """What is left after learned rules have auto-resolved what they can.

        This is the series checkpoint 3's chart plots and the number its done
        condition is about. It is a different number from ``review_rate`` and the
        report prints both, because a decline that came from widening a tolerance and
        a decline that came from learning look identical in one column and obvious in
        two.
        """
        return pct(max(self.flagged - self.auto_resolved, 0), self.settlement_rows)

    @property
    def records_per_second(self) -> Decimal:
        if self.seconds <= 0:
            return ZERO
        return (Decimal(self.records_processed) / Decimal(str(self.seconds))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "records_processed": self.records_processed,
            "settlement_rows": self.settlement_rows,
            "buckets": {
                "matched": self.matched, "variance": self.variance,
                "unmatched": self.unmatched, "quarantined": self.quarantined,
            },
            "auto_match_rate_pct": str(self.auto_match_rate),
            "review_rate_pct": str(self.review_rate),
            "net_review_rate_pct": str(self.net_review_rate),
            "review_queue": self.review_queue,
            "aged_findings": self.aged_findings,
            "auto_resolved": self.auto_resolved,
            "carried_forward": self.carried_forward,
            "exception_impact_inr": str(self.exception_impact_inr),
            "llm": self.usage.to_json(),
            "cost_inr": str(self.cost_inr),
            "cost_per_transaction_inr": str(self.cost_per_transaction_inr),
        }


def is_open_exception(reason: Reason) -> bool:
    """Does this verdict put something in front of a human?"""
    return reason is not CARRIED


def batch_metrics(
    result: BatchResult,
    *,
    records_processed: int,
    usage: LlmUsage,
    pricing: Pricing,
    aging: Aging,
    resolved: frozenset[tuple[str, str]],
    seconds: float,
) -> BatchMetrics:
    """Roll one reconciliation run up into the numbers the report prints.

    ``resolved`` is the ``(table, row_id)`` set a learned rule closed without a
    human. Empty until checkpoint 3, and subtracted here rather than inside the
    matcher, which must not know that anything downstream resolves anything.
    """
    settlement = result.counts("settlement_report")
    open_exceptions = [
        verdict
        for verdict in result.verdicts
        if verdict.bucket in (Bucket.VARIANCE, Bucket.UNMATCHED, Bucket.QUARANTINED)
        and is_open_exception(verdict.reason)
    ]
    new_findings = [v for v in open_exceptions if aging.is_new(result.batch, v)]
    matched = settlement["matched"]
    return BatchMetrics(
        batch=result.batch,
        records_processed=records_processed,
        settlement_rows=sum(settlement.values()),
        matched=matched,
        variance=settlement["variance"],
        unmatched=settlement["unmatched"],
        quarantined=settlement["quarantined"],
        review_queue=len(new_findings),
        aged_findings=len(open_exceptions) - len(new_findings),
        auto_resolved=sum(
            1
            for verdict in result.verdicts
            if verdict.table == "settlement_report"
            and (verdict.table, verdict.row_id) in resolved
        ),
        carried_forward=sum(1 for v in result.verdicts if v.reason is CARRIED),
        # Only new findings carry impact: an order that has been overdue for four
        # batches is one shortfall, not four.
        exception_impact_inr=sum((v.impact_inr for v in new_findings), ZERO),
        usage=usage,
        cost_inr=cost_inr(usage, pricing),
        cost_per_transaction_inr=per_transaction_inr(usage, pricing, matched),
        seconds=seconds,
    )


@dataclass(frozen=True)
class QuarantineSummary:
    """How many rows the models refused, and for what."""

    total: int
    by_reason: tuple[tuple[str, int], ...]
    by_batch: tuple[tuple[int, int], ...]


def quarantine_summary(results: list[BatchResult]) -> QuarantineSummary:
    reasons: Counter[str] = Counter()
    batches: Counter[int] = Counter()
    for result in results:
        for verdict in result.verdicts:
            if verdict.bucket is Bucket.QUARANTINED:
                reasons[verdict.reason.value] += 1
                batches[result.batch] += 1
    return QuarantineSummary(
        total=sum(reasons.values()),
        by_reason=tuple(sorted(reasons.items())),
        by_batch=tuple(sorted(batches.items())),
    )


@dataclass(frozen=True)
class AutoResolution:
    """One row the system resolved without a human. None exist yet.

    Checkpoint 3's rule application produces these; the harness scores the proposed
    cause against the answer key. Until then ``auto_resolution_precision`` returns
    ``None`` rather than 1.0, because a precision of 1.0 over zero attempts is the
    most flattering way to say nothing happened.
    """

    batch: int
    table: str
    row_id: str
    proposed_cause: str


def resolved_row_keys(proposals: list[AutoResolution]) -> frozenset[tuple[str, str]]:
    """The rows a learned rule closed, addressable the way verdicts are."""
    return frozenset((proposal.table, proposal.row_id) for proposal in proposals)


def auto_resolution_precision(
    proposals: list[AutoResolution], cause_by_row: dict[tuple[str, str], str]
) -> Decimal | None:
    """Share of auto-resolutions whose proposed cause matches the answer key."""
    if not proposals:
        return None
    correct = sum(
        1
        for proposal in proposals
        if cause_by_row.get((proposal.table, proposal.row_id)) == proposal.proposed_cause
    )
    return pct(correct, len(proposals))
