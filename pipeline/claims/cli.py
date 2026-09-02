"""`make claims` -- the claims queue as an operator sees it.

Sorted by expiry, never by creation date, with the summary header on top. The one
line above the list is the product: rupees open, how many claims, and how soon the
nearest window shuts.
"""

from __future__ import annotations

import argparse

from pipeline.claims.models import Claim, ClaimStatus
from pipeline.claims.queue import QueueRow, build
from pipeline.config import batch_window, generation
from pipeline.learn import run

DIVIDER = "-" * 96


def _clock(row: QueueRow) -> str:
    if row.days_remaining is None:
        return "no window"
    if row.days_remaining < 0:
        return f"{abs(row.days_remaining)}d over"
    return f"{row.days_remaining}d left"


def render_rows(rows: tuple[QueueRow, ...]) -> list[str]:
    lines = [
        f"{'claim':<10}{'platform':<10}{'cause':<28}{'amount':>12}"
        f"{'status':>10}{'deadline':>13}{'clock':>12}",
        DIVIDER,
    ]
    for row in rows:
        claim = row.claim
        deadline = "—" if claim.deadline.on is None else claim.deadline.on.isoformat()
        lines.append(
            f"{claim.claim_id:<10}{claim.platform:<10}{claim.cause:<28}"
            f"{'₹' + format(claim.amount_inr, ',.2f'):>12}{claim.status.value:>10}"
            f"{deadline:>13}{_clock(row):>12}"
        )
    return lines


def closed_summary(claims: list[Claim]) -> list[str]:
    recovered = [c for c in claims if c.status is ClaimStatus.RECOVERED]
    expired = [c for c in claims if c.status is ClaimStatus.EXPIRED]
    lines = [
        "",
        f"closed: {len(recovered)} recovered, {len(expired)} expired.",
    ]
    for claim in recovered[:5]:
        lines.append(
            f"  {claim.claim_id}  recovered in batch {claim.recovered_batch} against "
            f"{claim.recovery_row_id} (₹{claim.recovered_amount_inr})"
        )
    for claim in expired[:5]:
        lines.append(
            f"  {claim.claim_id}  expired on {claim.deadline.on} — ₹{claim.amount_inr} "
            "no longer recoverable"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the claims queue, sorted by expiry.")
    parser.add_argument("--offline", action="store_true", help="answer only from data/llm_cache")
    parser.add_argument("--draft", help="print the drafted message for one claim id")
    args = parser.parse_args()

    record = run(allow_network=not args.offline)
    as_of = batch_window(int(generation()["batch_count"]))[1]
    view = build(record.register.claims, as_of)

    if args.draft:
        claim = record.register.get(args.draft)
        print(claim.draft or f"{claim.claim_id} has no draft ({claim.resolution_class})")
        return 0

    print(view.header)
    print()
    print("\n".join(render_rows(view.rows)))
    print("\n".join(closed_summary(record.register.claims)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
