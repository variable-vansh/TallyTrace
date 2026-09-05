"""The queue view: sorted by expiry, not by creation date.

The sort order is the entire argument. A claims list ordered by when it was raised
puts the newest work on top and buries the claim that closes on Thursday, which is
how a seller loses a SAFE-T window they were already looking at. Ordering by expiry
means the top of the list is always the thing that stops being recoverable soonest.

Claims with no configured filing window sort last rather than first. They are not
urgent -- they are unclocked, and putting an unclocked claim above one with four days
left would be the same failure in the other direction.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from pipeline.claims.clock import (
    UNCLOCKED,
    BucketConfig,
    bucket_config_from,
    bucket_for,
    is_escalated,
)
from pipeline.claims.models import Claim
from pipeline.config import thresholds

ZERO = Decimal("0.00")

#: Sorts after every real date, so unclocked claims land at the bottom of the queue.
NO_CLOCK = date.max


@dataclass(frozen=True)
class QueueRow:
    """One claim as the queue shows it."""

    claim: Claim
    days_remaining: int | None
    bucket: str = UNCLOCKED

    @property
    def escalated(self) -> bool:
        return is_escalated(self.bucket)

    def to_json(self) -> dict[str, Any]:
        return {
            **self.claim.to_json(),
            "days_remaining": self.days_remaining,
            "bucket": self.bucket,
            "escalated": self.escalated,
        }


@dataclass(frozen=True)
class QueueView:
    """The open claims, in expiry order, plus the one line above them."""

    as_of: date
    rows: tuple[QueueRow, ...]
    total_inr: Decimal
    soonest_days: int | None
    expiring_count: int
    unclocked_count: int
    #: How many open claims sit in each bucket. The amber and red counts are the
    #: escalation signal -- see ``pipeline/claims/clock.py`` on why urgency is a
    #: bucket and not a status.
    bucket_counts: tuple[tuple[str, int], ...] = ()

    @property
    def escalated_count(self) -> int:
        return sum(count for bucket, count in self.bucket_counts if is_escalated(bucket))

    @property
    def header(self) -> str:
        """The line at the top of the queue. Every number in it is computed."""
        money = f"₹{self.total_inr:,.2f} open across {len(self.rows)} claim"
        money += "" if len(self.rows) == 1 else "s"
        if self.soonest_days is None:
            return f"{money} · none on a filing clock"
        days = "today" if self.soonest_days == 0 else f"in {self.soonest_days} days"
        return f"{money} · {self.expiring_count} expiring {days}"

    def to_json(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "header": self.header,
            "total_inr": str(self.total_inr),
            "open_claims": len(self.rows),
            "soonest_days": self.soonest_days,
            "expiring_count": self.expiring_count,
            "unclocked_count": self.unclocked_count,
            "bucket_counts": {bucket: count for bucket, count in self.bucket_counts},
            "escalated_count": self.escalated_count,
            "rows": [row.to_json() for row in self.rows],
        }


def build(claims: Iterable[Claim], as_of: date, buckets: BucketConfig | None = None) -> QueueView:
    """Every open claim, soonest expiry first, each in the bucket its clock puts it in.

    ``buckets`` defaults to the shipped policy so that every caller renders the same
    urgency the clock job stamped. Passing one is how a test asks what a different
    boundary would look like.
    """
    cfg = buckets or bucket_config_from(thresholds())
    rows = [
        QueueRow(
            claim=claim,
            days_remaining=claim.days_remaining(as_of),
            bucket=bucket_for(claim.days_remaining(as_of), cfg),
        )
        for claim in claims
        if claim.is_open
    ]
    rows.sort(key=lambda row: (row.claim.deadline.on or NO_CLOCK, row.claim.claim_id))

    clocked = [row.days_remaining for row in rows if row.days_remaining is not None]
    soonest = min(clocked) if clocked else None
    tally = Counter(row.bucket for row in rows)
    return QueueView(
        as_of=as_of,
        rows=tuple(rows),
        total_inr=sum((row.claim.amount_inr for row in rows), ZERO),
        soonest_days=soonest,
        expiring_count=sum(1 for day in clocked if day == soonest),
        unclocked_count=sum(1 for row in rows if row.days_remaining is None),
        bucket_counts=tuple(sorted(tally.items())),
    )
