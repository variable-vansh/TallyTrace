"""New findings versus aged ones.

A settlement row appears in exactly one batch. A ledger order does not: the open
book carries it until it settles, so an order that goes overdue in batch 5 and is
never paid is correctly reported as unmatched in batches 5 through 10. Six verdicts,
one problem.

That is right for the bucket contract -- the ledger row is an input in every one of
those batches and every input row gets a verdict -- and wrong for a review queue.
Nobody works the same exception six times, and counting it six times makes the queue
look like it is growing when it is only failing to shrink.

So the split lives here, in the reporting layer, rather than in the matcher: a
finding is **new** the first time a given row carries a given reason, and **aged**
every batch after. The matcher stays a pure function of one batch; the harness,
which sees all ten, is the thing that can tell the difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from pipeline.matcher import BatchResult, Verdict


@dataclass(frozen=True)
class Aging:
    """Which verdicts are first sightings, and which are the same problem again."""

    first_seen: dict[tuple[str, str, str], int]

    def is_new(self, batch: int, verdict: Verdict) -> bool:
        key = (verdict.table, verdict.row_id, verdict.reason.value)
        return self.first_seen.get(key) == batch

    def age_in_batches(self, batch: int, verdict: Verdict) -> int:
        key = (verdict.table, verdict.row_id, verdict.reason.value)
        return batch - self.first_seen.get(key, batch)


def index(results: Iterable[BatchResult]) -> Aging:
    """Record the first batch in which each row carried each reason."""
    first_seen: dict[tuple[str, str, str], int] = {}
    for result in sorted(results, key=lambda r: r.batch):
        for verdict in result.verdicts:
            key = (verdict.table, verdict.row_id, verdict.reason.value)
            first_seen.setdefault(key, result.batch)
    return Aging(first_seen=first_seen)
