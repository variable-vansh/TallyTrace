"""The daily clock: recompute what is left, rebucket, expire. Idempotent by construction.

A claims queue is only worth having if the clock on it is right *today*, so this runs
on a schedule rather than only when a batch lands. It does three things and nothing
else: work out how many days each open claim has left, put it in a bucket, and expire
the ones whose window has closed.

**Buckets are sized to the batch cadence, not to calendar intuition.** Batches are
weekly. A three-day red bucket would therefore never be observed -- a claim would pass
from comfortable to expired between two runs without ever being shown as urgent -- so
red is one batch left and amber is two. Both numbers are in
``config/thresholds.yaml``; neither is a literal in this file.

**Running it twice on the same day is a no-op.** Not by luck and not by a dedupe
further down: the register records the last date it ticked and refuses to tick again
for that date or an earlier one. A clock job that double-expires a claim on a retry
would turn a retry into a write-off, and retries are the normal condition of anything
that runs on a schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

#: Buckets, most urgent first. ``expired`` is terminal; ``unclocked`` is a claim on no
#: filing window at all, which is not the same as one with plenty of time.
RED = "red"
AMBER = "amber"
GREEN = "green"
UNCLOCKED = "unclocked"
EXPIRED = "expired"

#: Buckets that mean somebody has to look now. ``escalated`` is this condition, not a
#: status: a claim does not stop being open because its clock got short.
ESCALATED_BUCKETS = frozenset({RED, AMBER})


@dataclass(frozen=True)
class BucketConfig:
    """The day boundaries between buckets."""

    red_within_days: int
    amber_within_days: int


def bucket_config_from(thresholds: dict[str, Any]) -> BucketConfig:
    section = thresholds["claims"]["buckets"]
    red = int(section["red_within_days"])
    amber = int(section["amber_within_days"])
    if amber < red:
        raise ValueError(
            f"amber_within_days ({amber}) is inside red_within_days ({red}); "
            "amber is the wider window and a claim cannot be amber before it is red"
        )
    return BucketConfig(red_within_days=red, amber_within_days=amber)


def bucket_for(days_remaining: int | None, cfg: BucketConfig) -> str:
    """Which bucket a claim with this much time left belongs in."""
    if days_remaining is None:
        return UNCLOCKED
    if days_remaining < 0:
        return EXPIRED
    if days_remaining <= cfg.red_within_days:
        return RED
    if days_remaining <= cfg.amber_within_days:
        return AMBER
    return GREEN


def is_escalated(bucket: str) -> bool:
    """Amber or red. See the module docstring on why this is a bucket, not a status."""
    return bucket in ESCALATED_BUCKETS


@dataclass(frozen=True)
class ClockTick:
    """What one run of the clock did."""

    as_of: date
    ran: bool                       # False when this date had already been ticked
    expired: tuple[str, ...] = ()
    buckets: tuple[tuple[str, str], ...] = ()   # (claim_id, bucket), open claims only

    @property
    def escalated(self) -> tuple[str, ...]:
        return tuple(claim_id for claim_id, bucket in self.buckets if is_escalated(bucket))

    def to_json(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "ran": self.ran,
            "expired": list(self.expired),
            "buckets": {claim_id: bucket for claim_id, bucket in self.buckets},
            "escalated": list(self.escalated),
        }
