"""Verdicts and results -- the matcher's output shape.

One verdict per input row, one bucket per verdict, one reason per bucket. The
``detail`` map carries the numbers behind the reason (what was expected, what was
seen, what the tolerance was) as strings, so a verdict is JSON-serialisable without
a custom encoder and a Decimal never becomes a float on the way to disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from pipeline.matcher.reasons import Bucket, Reason


def detail(**values: Any) -> dict[str, str]:
    """Build a verdict detail map. Decimals go in as text, never as float."""
    out: dict[str, str] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, float):
            raise TypeError(f"float in verdict detail {key!r}")
        out[key] = str(value)
    return out


@dataclass(frozen=True)
class Verdict:
    """Where one input row landed, and why."""

    table: str                       # settlement_report | bank_statement | internal_ledger
    row_id: str                      # entity_id, utr, or order_id
    bucket: Bucket
    reason: Reason
    detail: Mapping[str, str] = field(default_factory=dict)
    order_id: str | None = None
    channel: str | None = None
    impact_inr: Decimal = Decimal("0.00")   # rupees the verdict puts in question

    def to_json(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "row_id": self.row_id,
            "bucket": self.bucket.value,
            "reason": self.reason.value,
            "order_id": self.order_id,
            "channel": self.channel,
            "impact_inr": str(self.impact_inr),
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class GroupFinding:
    """The N:1 verdict for one settlement group / bank credit pair."""

    utr: str
    settlement_sum: Decimal
    bank_amount: Decimal | None
    shortfall: Decimal               # settlement_sum - bank_amount; signed
    ties_out: bool
    explained_row_ids: list[str]     # subset that accounts for the credit
    residual_row_ids: list[str]      # rows the credit does not account for
    candidate_row_ids: list[str]     # the whole group, for the human to look at
    search_exhausted: bool = False   # searched for a residual subset and found none


@dataclass(frozen=True)
class BatchResult:
    """Everything one reconciliation run has to say about one batch."""

    batch: int
    verdicts: list[Verdict]
    groups: list[GroupFinding]
    # Orders closed by this run, mapped to the payout date that closed them. The date
    # is what lets a later batch say how long after the sale a deduction arrived --
    # which is the difference between learning "this is a lagged reversal" and
    # learning "Flipkart rows are reversals", and only one of those generalises.
    settled_orders: Mapping[str, date]

    def by_table(self, table: str) -> list[Verdict]:
        return [v for v in self.verdicts if v.table == table]

    def counts(self, table: str | None = None) -> dict[str, int]:
        rows = self.verdicts if table is None else self.by_table(table)
        counts = {bucket.value: 0 for bucket in Bucket}
        for verdict in rows:
            counts[verdict.bucket.value] += 1
        return counts

    def to_json(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "counts": {
                "all": self.counts(),
                "settlement_report": self.counts("settlement_report"),
                "bank_statement": self.counts("bank_statement"),
                "internal_ledger": self.counts("internal_ledger"),
            },
            "verdicts": [verdict.to_json() for verdict in self.verdicts],
            "groups": [
                {
                    "utr": group.utr,
                    "settlement_sum": str(group.settlement_sum),
                    "bank_amount": None if group.bank_amount is None else str(group.bank_amount),
                    "shortfall": str(group.shortfall),
                    "ties_out": group.ties_out,
                    "explained_row_ids": group.explained_row_ids,
                    "residual_row_ids": group.residual_row_ids,
                    "candidate_row_ids": group.candidate_row_ids,
                    "search_exhausted": group.search_exhausted,
                }
                for group in self.groups
            ],
        }


def assert_one_bucket_each(verdicts: Iterable[Verdict]) -> None:
    """Every input row lands in exactly one bucket. Asserted, not assumed."""
    seen: set[tuple[str, str]] = set()
    for verdict in verdicts:
        key = (verdict.table, verdict.row_id)
        if key in seen:
            raise ValueError(f"{key} received more than one verdict")
        seen.add(key)
