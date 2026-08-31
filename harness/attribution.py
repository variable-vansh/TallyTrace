"""Where did each injected trouble actually land?

This is the module the checkpoint calls "the number that tells you your tolerance
band is wrong". For every injected row it asks the matcher's own output one
question -- which bucket, under which reason code -- and reports the answer without
editorialising.

Two subtleties are load-bearing:

**A row is not always in the batch its trouble was recorded in.** An RTO reversal is
recorded in the batch it lands in, a lagged settlement leaves its batch entirely. So
the lookup is over the whole corpus, and the batch the row was *found* in is reported
alongside the batch it was *injected* into.

**A row is not always in the report at all.** ``missing_settlement_row`` deletes the
settlement row: that is the trouble. Attribution then falls back to the verdict on
the affected order, and records that it did, because "the matcher never saw a row for
this" and "the matcher saw the row and cleared it" are opposite findings and must not
average together.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from harness.truth import AnswerKey, Injection
from pipeline.matcher import BatchResult, Bucket, Reason, Verdict
from pipeline.matcher.reasons import SEVERITY

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class RowOutcome:
    """One injected row, and the verdict the matcher gave whatever it could see."""

    cause: str
    resolution_class: str
    injected_batch: int
    row_id: str
    found_in_batch: int | None
    bucket: Bucket | None
    reason: Reason | None
    via: str                      # row | order | absent
    observed_delta_inr: Decimal   # the largest deviation the matcher measured
    tolerance_inr: Decimal        # the band it measured against
    days_late: int

    @property
    def silently_cleared(self) -> bool:
        """The matcher looked at an injected trouble and called it clean."""
        return self.bucket is Bucket.MATCHED

    @property
    def caught(self) -> bool:
        return self.bucket in (Bucket.VARIANCE, Bucket.UNMATCHED, Bucket.QUARANTINED)


def _decimal(verdict: Verdict, key: str) -> Decimal:
    raw = verdict.detail.get(key)
    return abs(Decimal(raw)) if raw else ZERO


def _measurement(verdict: Verdict | None) -> tuple[Decimal, Decimal, int]:
    """What the matcher measured on this row: worst delta, band, days late."""
    if verdict is None:
        return ZERO, ZERO, 0
    delta = max(
        _decimal(verdict, "fee_delta"),
        _decimal(verdict, "net_delta"),
        _decimal(verdict, "shortfall"),
    )
    late = int(verdict.detail.get("days_late", "0"))
    return delta, _decimal(verdict, "tolerance_inr"), late


class VerdictIndex:
    """Every verdict in the corpus, addressable by table and row id."""

    def __init__(self, results: Iterable[BatchResult]) -> None:
        self._rows: dict[tuple[str, str], tuple[int, Verdict]] = {}
        for result in results:
            for verdict in result.verdicts:
                self._rows[(verdict.table, verdict.row_id)] = (result.batch, verdict)

    def find(self, table: str, row_id: str) -> tuple[int, Verdict] | None:
        return self._rows.get((table, row_id))


def _table_for(injection: Injection) -> str:
    return "bank_statement" if injection.is_bank_side else "settlement_report"


def _order_fallback(
    injection: Injection, position: int, index: VerdictIndex
) -> tuple[int, Verdict] | None:
    """What the matcher made of the order behind a row that is not in any report.

    The generator writes both id lists sorted, and the injectors that delete a row
    append one order per row, so equal lengths mean position ``n`` in one list
    belongs with position ``n`` in the other. When the lengths differ that pairing
    is not available, and rather than guess, the fallback takes the most severe
    verdict across the injection's orders -- blurring which order, never whether.
    """
    orders = injection.affected_order_ids
    if len(orders) == len(injection.affected_row_ids) and position < len(orders):
        return index.find("internal_ledger", orders[position])

    found = [
        hit for hit in (index.find("internal_ledger", order) for order in orders) if hit
    ]
    if not found:
        return None
    return max(found, key=lambda hit: SEVERITY[hit[1].bucket])


def _outcome(
    injection: Injection, row_id: str, position: int, index: VerdictIndex
) -> RowOutcome:
    """Resolve one affected row to a verdict, falling back to its order."""
    found = index.find(_table_for(injection), row_id)
    via = "row"
    if found is None:
        # The row is not in any report. For a deleted settlement row that absence is
        # itself the trouble, so ask what the matcher made of the order instead.
        via = "order"
        found = _order_fallback(injection, position, index)
    if found is None:
        return RowOutcome(
            cause=injection.cause, resolution_class=injection.resolution_class,
            injected_batch=injection.batch, row_id=row_id, found_in_batch=None,
            bucket=None, reason=None, via="absent",
            observed_delta_inr=ZERO, tolerance_inr=ZERO, days_late=0,
        )

    batch, verdict = found
    delta, tolerance, late = _measurement(verdict)
    return RowOutcome(
        cause=injection.cause, resolution_class=injection.resolution_class,
        injected_batch=injection.batch, row_id=row_id, found_in_batch=batch,
        bucket=verdict.bucket, reason=verdict.reason, via=via,
        observed_delta_inr=delta, tolerance_inr=tolerance, days_late=late,
    )


def attribute(results: Iterable[BatchResult], key: AnswerKey) -> list[RowOutcome]:
    """Resolve every injected row in the answer key to a matcher verdict."""
    index = VerdictIndex(results)
    return [
        _outcome(injection, row_id, position, index)
        for injection in key.injections
        for position, row_id in enumerate(injection.affected_row_ids)
    ]


# --------------------------------------------------------------------------- #
# Roll-ups
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CauseConfusion:
    """One cause, and the buckets its injected rows actually landed in."""

    cause: str
    resolution_class: str
    rows: int
    by_verdict: tuple[tuple[str, int], ...]   # "bucket/reason" -> count, most common first
    caught: int
    silently_cleared: int

    @property
    def catch_rate(self) -> Decimal:
        return Decimal(self.caught) / Decimal(self.rows) if self.rows else ZERO


def confusion(outcomes: Iterable[RowOutcome]) -> list[CauseConfusion]:
    """The cause-level confusion table, one row per injected cause."""
    grouped: dict[str, list[RowOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.cause, []).append(outcome)

    table: list[CauseConfusion] = []
    for cause in sorted(grouped):
        rows = grouped[cause]
        labels = Counter(
            f"{o.bucket.value}/{o.reason.value}" if o.bucket and o.reason else "not_seen"
            for o in rows
        )
        table.append(
            CauseConfusion(
                cause=cause,
                resolution_class=rows[0].resolution_class,
                rows=len(rows),
                by_verdict=tuple(sorted(labels.items(), key=lambda kv: (-kv[1], kv[0]))),
                caught=sum(1 for o in rows if o.caught),
                silently_cleared=sum(1 for o in rows if o.silently_cleared),
            )
        )
    return table


@dataclass(frozen=True)
class SilentClears:
    """Injected troubles the matcher called clean, and how close they came to firing.

    The count on its own does not say whether a band is too wide. Two numbers do.
    ``largest_delta_inr`` is the biggest deviation that was cleared anyway; and
    ``tightest_headroom_inr`` is the smallest gap between a cleared row's deviation
    and the band that permitted it. A cause clearing with ₹0.02 of headroom is a band
    about to break; one clearing at ₹0.00 deviation had no money in it to find.
    """

    cause: str
    rows: int
    largest_delta_inr: Decimal
    tightest_headroom_inr: Decimal | None
    tolerance_at_tightest_inr: Decimal | None
    largest_days_late: int


def _tightest(rows: list[RowOutcome]) -> RowOutcome | None:
    """The cleared row closest to firing. Rows with no measured band cannot be close."""
    measured = [row for row in rows if row.tolerance_inr > ZERO]
    if not measured:
        return None
    return min(measured, key=lambda o: (o.tolerance_inr - o.observed_delta_inr, o.row_id))


def silent_clears(outcomes: Iterable[RowOutcome]) -> list[SilentClears]:
    grouped: dict[str, list[RowOutcome]] = {}
    for outcome in outcomes:
        if outcome.silently_cleared:
            grouped.setdefault(outcome.cause, []).append(outcome)

    table: list[SilentClears] = []
    for cause in sorted(grouped):
        rows = grouped[cause]
        tightest = _tightest(rows)
        table.append(
            SilentClears(
                cause=cause,
                rows=len(rows),
                largest_delta_inr=max(o.observed_delta_inr for o in rows),
                tightest_headroom_inr=(
                    None if tightest is None
                    else tightest.tolerance_inr - tightest.observed_delta_inr
                ),
                tolerance_at_tightest_inr=None if tightest is None else tightest.tolerance_inr,
                largest_days_late=max(o.days_late for o in rows),
            )
        )
    return sorted(table, key=lambda s: (-s.rows, s.cause))
