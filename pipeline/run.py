"""The runner: walk the ten batches, reconcile each, carry the open book forward.

This is the only place that knows a corpus has more than one batch. It holds the
state the matcher deliberately does not -- which orders are still open, which were
settled in an earlier week -- and hands each run a self-contained universe.

Carrying the book forward is not an optimisation. A settlement row lands in the
batch its payout fell into and a ledger row in the batch the order was booked in, so
an order booked in batch 3 and paid in batch 5 lives in two different files.
Reconciling batch 5 against batch 5's ledger alone would leave four fifths of the
settlement report unmatched by construction.

Reads ``data/generated`` and nothing else. The answer key is the harness's
business; the boundary test in ``tests/test_boundaries.py`` fails if anything under
``pipeline/`` so much as names its path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from pipeline.config import REPO_ROOT, batch_window, generation, thresholds
from pipeline.loader import BatchTables, load_batch
from pipeline.matcher import BatchResult, MatchConfig, OpenOrder, ReconInput, match_config_from, reconcile
from pipeline.matcher.reasons import Bucket, Reason

DEFAULT_OUTPUT = REPO_ROOT / "data" / "reconciliation.json"


@dataclass
class OpenBook:
    """Orders booked and not yet settled, and orders already closed.

    Closed orders keep the date they settled on, so a deduction arriving three
    cycles later can say how many days late it is rather than only that it is late.

    Overdue orders stay open rather than being retired: money that is still missing
    is still missing, and a settlement that finally arrives three cycles late has to
    have something to match against.
    """

    open_orders: dict[str, OpenOrder]
    closed: dict[str, date]

    @classmethod
    def empty(cls) -> "OpenBook":
        return cls(open_orders={}, closed={})

    def admit(self, tables: BatchTables) -> None:
        window_end = batch_window(tables.batch)[1]
        for row in tables.ledger:
            if row.order_id in self.closed:
                continue
            self.open_orders[row.order_id] = OpenOrder(
                ledger=row, booked_batch=tables.batch, booked_window_end=window_end
            )

    def close(self, settled: Mapping[str, date]) -> None:
        for order_id, settled_on in settled.items():
            self.closed[order_id] = settled_on
            self.open_orders.pop(order_id, None)


def run_batch(tables: BatchTables, book: OpenBook, cfg: MatchConfig) -> BatchResult:
    """Reconcile one batch against the book as it stands, then update the book."""
    book.admit(tables)
    result = reconcile(
        ReconInput(
            batch=tables.batch,
            batch_end=batch_window(tables.batch)[1],
            settlements=tables.settlements,
            bank=tables.bank,
            open_orders=sorted(book.open_orders.values(), key=lambda o: o.order_id),
            closed_orders=dict(book.closed),
            quarantined=tables.quarantined,
        ),
        cfg,
    )
    book.close(result.settled_orders)
    return result


def run_all(generated_dir: Path | None = None, last_batch: int | None = None) -> list[BatchResult]:
    """Reconcile batches 1..N in order, carrying the open book across them."""
    cfg = match_config_from(thresholds())
    count = last_batch or int(generation()["batch_count"])
    book = OpenBook.empty()
    return [run_batch(load_batch(batch, generated_dir), book, cfg) for batch in range(1, count + 1)]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def summarise(result: BatchResult) -> str:
    counts = result.counts("settlement_report")
    total = sum(counts.values()) or 1
    return (
        f"batch {result.batch:>2}  "
        f"settlement rows {total:>4}  "
        f"matched {counts['matched']:>4} ({counts['matched'] * 100 // total:>3}%)  "
        f"variance {counts['variance']:>3}  "
        f"unmatched {counts['unmatched']:>3}  "
        f"quarantined {result.counts()['quarantined']:>2}  "
        f"groups {len(result.groups):>3} "
        f"({sum(1 for g in result.groups if not g.ties_out)} not tying out)"
    )


def main() -> int:
    results = run_all()
    for result in results:
        print(summarise(result))

    payload = {"batches": [result.to_json() for result in results]}
    DEFAULT_OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {DEFAULT_OUTPUT.relative_to(REPO_ROOT)}")
    # An order still inside its settlement window is carried, not queued: it is not
    # a human's problem until the window has actually elapsed.
    unresolved = sum(
        1
        for result in results
        for verdict in result.verdicts
        if verdict.bucket in (Bucket.VARIANCE, Bucket.UNMATCHED)
        and verdict.reason is not Reason.AWAITING_SETTLEMENT_IN_WINDOW
    )
    print(f"{unresolved} rows need a human across all batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
